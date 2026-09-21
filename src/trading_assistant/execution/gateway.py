"""唯一允许提交 NT 订单的执行 Strategy。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from functools import partial
from math import floor, isfinite
from pathlib import Path
from typing import Any, Literal

from nautilus_trader.common.component import TimeEvent
from nautilus_trader.config import StrategyConfig
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.execution.reports import ExecutionMassStatus, FillReport, OrderStatusReport
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import OrderSide, OrderStatus, TimeInForce
from nautilus_trader.model.events import (
    OrderAccepted,
    OrderCanceled,
    OrderCancelRejected,
    OrderDenied,
    OrderExpired,
    OrderFilled,
    OrderRejected,
    OrderSubmitted,
)
from nautilus_trader.model.identifiers import AccountId, ClientId, ClientOrderId, InstrumentId
from nautilus_trader.model.objects import Currency
from nautilus_trader.model.orders import Order
from nautilus_trader.trading.strategy import Strategy
from sqlalchemy.exc import SQLAlchemyError

from trading_assistant.data.corporate_actions import CorporateActionRepository
from trading_assistant.data.factor import expected_factor_date, factor_execution_window
from trading_assistant.data.market_calendar import CALENDAR_VERSION, shift_regular_session
from trading_assistant.execution.events import TRADE_SIGNAL_TOPIC, TradeSignalEvent
from trading_assistant.live.portfolio_snapshot import account_not_ready_reason
from trading_assistant.risk.checks import (
    apply_weight_limits,
    effective_strategy_equity,
    order_rejection_reason,
)
from trading_assistant.risk.config import RiskLimits
from trading_assistant.storage.models import OrderEventRecord
from trading_assistant.storage.repository import TradingRepository, utc_datetime_from_ns

GatewayDecision = Literal["EXPIRED", "RISK_REJECTED", "PENDING", "APPROVED"]


def gateway_decision(
    *, approval_mode: str, expired: bool, risk_rejection: str | None
) -> GatewayDecision:
    """把过期、风控与审批模式归并为唯一网关决策。"""
    if expired:
        return "EXPIRED"
    if risk_rejection is not None:
        return "RISK_REJECTED"
    if approval_mode == "manual":
        return "PENDING"
    if approval_mode == "auto":
        return "APPROVED"
    raise ValueError(f"Unsupported approval mode: {approval_mode}")


class ExecutionGatewayConfig(StrategyConfig, frozen=True):
    """执行网关配置。"""

    instrument_routes: dict[str, str]
    execution_bar_types: dict[str, str]
    approval_mode: str
    database_url: str
    account_id: str
    strategy_capital_usd: float
    max_order_notional_usd: float
    max_instrument_weight: float
    max_daily_new_positions: int
    max_gross_exposure: float
    backtest_run_id: str | None = None
    signal_topic: str = TRADE_SIGNAL_TOPIC
    signal_scope: str = "default"
    approval_poll_interval_seconds: int = 5
    bootstrap_from_catalog: bool = False
    catalog_client_id: str = "CATALOG"
    catalog_lookback_days: int = 2_200
    model_release_id: str | None = None
    max_factor_unscorable_fraction: float = 0.0
    max_factor_preserved_price_age_sessions: int = 0
    broker_account_stale_after_seconds: int | None = None
    corporate_action_path: str | None = None


@dataclass(frozen=True)
class _PlannedOrder:
    canonical_id: str
    instrument_id: str
    side: OrderSide
    quantity: int
    price: float
    target_weight: float
    opens_position: bool


@dataclass(frozen=True)
class _OrderContext:
    signal_event_id: str
    strategy_name: str
    direction: str
    quantity: float
    is_sell: bool
    signal_reason: str
    signal_timestamp_ns: int
    instrument_id: str = ""


class ExecutionGatewayStrategy(Strategy):  # type: ignore[misc]
    """消费目标权重。完成风控与审批后通过 NT 唯一提交订单。"""

    def __init__(self, config: ExecutionGatewayConfig) -> None:
        super().__init__(config)
        self._settings = config
        self._limits = RiskLimits(
            strategy_capital_usd=config.strategy_capital_usd,
            max_order_notional_usd=config.max_order_notional_usd,
            max_instrument_weight=config.max_instrument_weight,
            max_daily_new_positions=config.max_daily_new_positions,
            max_gross_exposure=config.max_gross_exposure,
            max_factor_unscorable_fraction=config.max_factor_unscorable_fraction,
            max_factor_preserved_price_age_sessions=config.max_factor_preserved_price_age_sessions,
        )
        self._repository: TradingRepository | None = None
        self._order_contexts: dict[str, _OrderContext] = {}
        self._pending_buys: dict[str, tuple[TradeSignalEvent, tuple[_PlannedOrder, ...]]] = {}
        self._open_sells: dict[str, set[str]] = {}
        self._failed_signals: set[str] = set()
        self._daily_new_positions: dict[str, int] = {}
        self._execution_bar_requests: set[str] = set()
        self._deferred_signals: dict[str, TradeSignalEvent] = {}
        self._factor_plan_summaries: dict[str, str] = {}
        self._execution_prices_ready = not config.bootstrap_from_catalog
        self._execution_price_date: date | None = None
        self._execution_request_started_ns: int | None = None
        self._execution_request_generation = 0
        self._broker_status: Callable[[], tuple[bool, bool]] | None = None
        self._startup_reports: list[object] = []
        self._recovery_error: str | None = None

    def bind_broker_status(self, status: Callable[[], tuple[bool, bool]]) -> None:
        """由节点装配共享券商状态, 不查询外部服务。"""
        self._broker_status = status
        self.msgbus.subscribe("reports.execution.*", self._on_execution_report)
        self.msgbus.subscribe("events.order.*", self._on_external_order_event)

    def _account_rejection(self) -> str | None:
        timeout = self._settings.broker_account_stale_after_seconds
        if timeout is None or self._settings.backtest_run_id is not None:
            return None
        if self._recovery_error is not None:
            return self._recovery_error
        connected, reconciled = (
            (None, None) if self._broker_status is None else self._broker_status()
        )
        return account_not_ready_reason(
            self.portfolio.account(account_id=AccountId(self._settings.account_id)),
            now_ns=self.clock.timestamp_ns(),
            stale_after_seconds=timeout,
            broker_connected=connected,
            reconciliation_complete=reconciled,
        )

    def on_start(self) -> None:
        """建立审计仓储并订阅领域事件。"""
        self._repository = TradingRepository(self._settings.database_url)
        self._repository.create_schema()
        for report in self._startup_reports:
            self._on_execution_report(report)
        self._startup_reports.clear()
        self._restore_daily_count()
        self.msgbus.subscribe(self._settings.signal_topic, self._handle_signal)
        if (
            self._settings.approval_mode == "manual"
            or self._settings.bootstrap_from_catalog
            or (
                self._settings.model_release_id is not None
                and self._settings.backtest_run_id is None
            )
        ):
            self.clock.set_timer(
                "approval-poll",
                interval=timedelta(seconds=self._settings.approval_poll_interval_seconds),
                callback=self._poll_approved_signal,
            )
        if self._settings.bootstrap_from_catalog:
            self._request_execution_bar_history()

    def _handle_signal(self, message: object) -> None:
        if not isinstance(message, TradeSignalEvent):
            return
        if self._settings.bootstrap_from_catalog and message.factor_context is not None:
            required_date = date.fromisoformat(message.factor_context.asof_date)
            if self._execution_price_date != required_date:
                self._execution_prices_ready = False
        if not self._execution_prices_ready:
            self._deferred_signals.setdefault(str(message.id), message)
            self.log.info(
                f"Deferred signal until execution price bootstrap completes: event_id={message.id}"
            )
            if not self._execution_bar_requests:
                self._request_execution_bar_history()
            return
        self._process_signal(message)

    def _process_signal(self, message: TradeSignalEvent) -> None:
        """在执行价已就绪后处理一个持久化交易信号。"""
        now_ns = max(self.clock.timestamp_ns(), message.ts_event)
        repository = self._require_repository()
        workflow = repository.get_signal_workflow(str(message.id))
        if workflow is None:
            self.log.error(f"Signal workflow not found: event_id={message.id}")
            return
        if workflow.scope != self._settings.signal_scope:
            self.log.error(f"Signal scope mismatch: event_id={message.id}")
            return
        if workflow.status != "NEW":
            self.log.info(
                f"Signal workflow already handled: event_id={message.id}, status={workflow.status}"
            )
            return
        if now_ns < message.expires_at_ns and self._account_rejection() is not None:
            self._deferred_signals[str(message.id)] = message
            return
        plan, rejection = self._build_plan(message)
        decision = gateway_decision(
            approval_mode=self._settings.approval_mode,
            expired=now_ns >= message.expires_at_ns,
            risk_rejection=rejection,
        )
        reason = {
            "EXPIRED": "signal expired",
            "RISK_REJECTED": rejection or "risk rejected",
            "PENDING": "manual approval required",
            "APPROVED": "auto approval",
        }[decision]
        transitioned = False
        # 回测在全部同刻开盘报价进入 NT 缓存后执行, 不命中上一日收盘价。
        execution_start_ns = message.not_before_ns + (
            1_000 if message.factor_context is not None and self._settings.backtest_run_id else 0
        )
        if decision in {"EXPIRED", "RISK_REJECTED"}:
            terminal_status: Literal["EXPIRED", "RISK_REJECTED"] = (
                "EXPIRED" if decision == "EXPIRED" else "RISK_REJECTED"
            )
            transitioned = repository.reject_new_signal(
                str(message.id),
                status=terminal_status,
                timestamp_ns=now_ns,
                risk_summary=reason,
            )
        elif decision == "PENDING":
            transitioned = repository.prepare_manual_approval(
                str(message.id),
                planned_orders=self._plan_payload(plan),
                risk_summary=self._factor_plan_summaries.get(
                    str(message.id), "initial risk check passed"
                ),
                timestamp_ns=now_ns,
            )
        elif now_ns < execution_start_ns:
            timer = f"factor-open-{message.id}"
            if timer not in self.clock.timer_names:
                self.clock.set_time_alert_ns(
                    timer,
                    execution_start_ns,
                    callback=lambda _: self._handle_signal(message),
                )
            return
        elif repository.claim_auto_signal(
            str(message.id),
            timestamp_ns=now_ns,
            planned_orders=self._plan_payload(plan),
            risk_summary=self._factor_plan_summaries.get(
                str(message.id), "auto approval; risk passed"
            ),
        ):
            transitioned = True
            self._execute_plan(message, plan)
            repository.finish_processing(
                str(message.id),
                status="ORDERS_SUBMITTED",
                timestamp_ns=now_ns,
                risk_summary=self._factor_plan_summaries.pop(str(message.id), None),
            )
        if transitioned and decision != "APPROVED":
            if rejection is not None:
                self._record_factor_skip(message, rejection)
            repository.record_approval(
                message,
                approval_mode=self._settings.approval_mode,
                decision=decision,
                reason=f"{message.reason}; {reason}",
                timestamp_ns=now_ns,
            )

    def _request_execution_bar_history(self) -> None:
        """通过 NT DataEngine 为所有策略统一加载 EXTERNAL 执行 Bar。"""
        if self._settings.catalog_lookback_days < 1:
            raise ValueError("catalog_lookback_days must be positive")
        end = self.clock.utc_now()
        self._execution_prices_ready = False
        self._execution_request_started_ns = self.clock.timestamp_ns()
        self._execution_request_generation += 1
        generation = self._execution_request_generation
        start = end - timedelta(days=self._settings.catalog_lookback_days)
        bar_types = tuple(dict.fromkeys(self._settings.execution_bar_types.values()))
        self._execution_bar_requests = set(bar_types)
        if not bar_types:
            self._complete_execution_bar_bootstrap()
            return
        client_id = ClientId(self._settings.catalog_client_id)
        for value in bar_types:
            try:
                self.request_bars(
                    BarType.from_str(value),
                    start=start,
                    end=end,
                    client_id=client_id,
                    callback=partial(
                        self._execution_bar_request_completed, value, generation=generation
                    ),
                )
            except Exception as exc:
                self._execution_request_generation += 1
                self._execution_bar_requests.clear()
                self.log.error(f"Execution price refresh failed: {type(exc).__name__}")
                return

    def _execution_bar_request_completed(
        self, bar_type: str, _: UUID4, *, generation: int | None = None
    ) -> None:
        if generation is not None and generation != self._execution_request_generation:
            return
        self._execution_bar_requests.discard(bar_type)
        if not self._execution_bar_requests:
            self._complete_execution_bar_bootstrap()

    def _complete_execution_bar_bootstrap(self) -> None:
        """结束执行价预热并恢复启动阶段已经落库的 NEW 信号。"""
        if self._execution_prices_ready:
            return
        missing = sorted(
            value
            for value in self._settings.execution_bar_types.values()
            if self.cache.bar(BarType.from_str(value)) is None
        )
        if missing:
            self.log.error(f"Execution price bootstrap missing BarTypes: {', '.join(missing)}")
        self._execution_prices_ready = True
        self._execution_price_date = expected_factor_date(
            datetime.fromtimestamp(self.clock.timestamp_ns() / 1e9, tz=UTC)
        )
        self._execution_request_started_ns = None

        deferred = tuple(self._deferred_signals.values())
        self._deferred_signals.clear()
        for event in deferred:
            self._process_signal(event)

        repository = self._require_repository()
        for workflow in repository.list_new_signal_workflows(scope=self._settings.signal_scope):
            self._process_signal(workflow.to_event())

    def _poll_approved_signal(self, _: TimeEvent) -> None:
        """领取人工确认信号并在提交前重新计算和风控。"""
        repository = self._require_repository()
        self._reconcile_factor_orders()
        now_ns = self.clock.timestamp_ns()
        for stale in repository.list_new_signal_workflows(scope=self._settings.signal_scope):
            if stale.expires_at_utc <= utc_datetime_from_ns(now_ns):
                self._deferred_signals.pop(stale.event_id, None)
                self._process_signal(stale.to_event())
        if self._settings.bootstrap_from_catalog:
            if (
                self._execution_request_started_ns is not None
                and self.clock.timestamp_ns() - self._execution_request_started_ns > 60_000_000_000
            ):
                self._execution_bar_requests.clear()
                self._execution_request_generation += 1
            if not self._execution_prices_ready:
                if not self._execution_bar_requests:
                    self._request_execution_bar_history()
                return
        if self._settings.approval_mode != "manual":
            pending = tuple(self._deferred_signals.values())
            self._deferred_signals.clear()
            for event in pending:
                self._handle_signal(event)
            for new_workflow in repository.list_new_signal_workflows(
                scope=self._settings.signal_scope
            ):
                self._handle_signal(new_workflow.to_event())
            return
        while True:
            now_ns = self.clock.timestamp_ns()
            workflow = repository.claim_next_approved(
                timestamp_ns=now_ns, scope=self._settings.signal_scope
            )
            if workflow is None:
                return
            event = workflow.to_event()
            if now_ns >= event.expires_at_ns:
                repository.finish_processing(
                    str(event.id),
                    status="EXPIRED",
                    timestamp_ns=now_ns,
                    risk_summary="signal expired after approval",
                )
                repository.record_approval(
                    event,
                    approval_mode="manual",
                    decision="EXPIRED",
                    reason=f"{event.reason}; signal expired after approval",
                    timestamp_ns=now_ns,
                )
                continue
            plan, rejection = self._build_plan(event)
            if rejection is not None:
                self._record_factor_skip(event, rejection)
                repository.finish_processing(
                    str(event.id),
                    status="RISK_REJECTED",
                    timestamp_ns=now_ns,
                    risk_summary=rejection,
                )
                repository.record_approval(
                    event,
                    approval_mode="manual",
                    decision="RISK_REJECTED",
                    reason=f"{event.reason}; second risk check: {rejection}",
                    timestamp_ns=now_ns,
                )
                continue
            repository.record_approval(
                event,
                approval_mode="manual",
                decision="APPROVED",
                reason=f"{event.reason}; user approved; second risk check passed",
                timestamp_ns=now_ns,
            )
            self._execute_plan(event, plan)
            repository.finish_processing(
                str(event.id),
                status="ORDERS_SUBMITTED",
                timestamp_ns=now_ns,
                risk_summary=self._factor_plan_summaries.pop(str(event.id), None),
            )

    @staticmethod
    def _plan_payload(plan: tuple[_PlannedOrder, ...]) -> tuple[dict[str, Any], ...]:
        return tuple(
            {
                "asset_id": item.canonical_id,
                "instrument_id": item.instrument_id,
                "side": "BUY" if item.side == OrderSide.BUY else "SELL",
                "quantity": item.quantity,
                "price": item.price,
                "target_weight": item.target_weight,
                "opens_position": item.opens_position,
            }
            for item in plan
        )

    def _record_opened_positions(self, plan: tuple[_PlannedOrder, ...], timestamp_ns: int) -> None:
        date_key = self._date_key(timestamp_ns)
        opened = sum(1 for item in plan if item.opens_position)
        self._daily_new_positions[date_key] = self._daily_new_positions.get(date_key, 0) + opened

    def _build_plan(self, event: TradeSignalEvent) -> tuple[tuple[_PlannedOrder, ...], str | None]:
        rejection = self._account_rejection() or self._factor_rejection(event, execution=False)
        if rejection is not None:
            return (), rejection
        requested = dict(event.target_weights)
        unknown_targets = sorted(set(requested) - self._settings.instrument_routes.keys())
        if unknown_targets:
            return (), f"unknown target instruments: {', '.join(unknown_targets)}"
        target_weights = apply_weight_limits(requested, self._limits)
        current_quantities = {
            canonical_id: self._current_quantity(InstrumentId.from_str(execution_id))
            for canonical_id, execution_id in self._settings.instrument_routes.items()
        }
        required_ids = {
            canonical_id for canonical_id, weight in target_weights.items() if weight > 0
        } | {canonical_id for canonical_id, quantity in current_quantities.items() if quantity != 0}
        if not required_ids:
            return (), None
        context = event.factor_context
        preserved = set(event.preserve_positions)
        if context is not None:
            if self._settings.corporate_action_path is not None:
                try:
                    actions = CorporateActionRepository(Path(self._settings.corporate_action_path))
                    execution_date = datetime.fromtimestamp(
                        event.not_before_ns / 1e9, tz=UTC
                    ).date()
                    for canonical in sorted(required_ids):
                        history = actions.read(canonical)
                        if history is not None and any(
                            date.fromisoformat(context.asof_date) < split.ex_date <= execution_date
                            for split in history.splits
                        ):
                            return (), f"cross-session split requires reconciliation: {canonical}"
                except (OSError, RuntimeError, ValueError):
                    return (), "corporate action reference unavailable"
            uncovered = required_ids - set(context.candidate_ids)
            if uncovered:
                return (), f"held instruments outside factor candidates: {sorted(uncovered)}"
            if self.cache.orders_open(strategy_id=self.id):
                return (), "open orders require reconciliation before factor rebalance"
            if self._settings.broker_account_stale_after_seconds is not None:
                ownership = self._ownership_rejection(current_quantities)
                if ownership is not None:
                    return (), ownership

        prices: dict[str, float] = {}
        for canonical_id in sorted(required_ids):
            bar_type = self._settings.execution_bar_types.get(canonical_id)
            if bar_type is None:
                return (), f"missing execution BarType for {canonical_id}"
            bar = self.cache.bar(BarType.from_str(bar_type))
            if bar is None:
                return (), f"missing price for {canonical_id}"
            prices[canonical_id] = bar.close.as_double()
            if not isfinite(prices[canonical_id]) or prices[canonical_id] <= 0:
                return (), f"invalid price for {canonical_id}"
            if context is not None:
                minimum_date = date.fromisoformat(context.asof_date)
                if canonical_id in preserved:
                    minimum_date = shift_regular_session(
                        minimum_date, -self._limits.max_factor_preserved_price_age_sessions
                    )
                bar_date = datetime.fromtimestamp(bar.ts_event / 1_000_000_000, tz=UTC).date()
                if (
                    bar_date < minimum_date
                    or bar.ts_init > self.clock.timestamp_ns()
                    or (
                        self._settings.backtest_run_id is None
                        and bar_date > date.fromisoformat(context.asof_date)
                    )
                ):
                    return (), f"stale or future execution price for {canonical_id}"

        equity = self._portfolio_equity(prices, current_quantities)
        if equity <= 0:
            return (), "non-positive portfolio equity"

        preserved_weights = {
            key: current_quantities[key] * prices[key] / equity for key in preserved & required_ids
        }
        try:
            target_weights = apply_weight_limits(
                requested, self._limits, preserved_weights=preserved_weights
            )
        except ValueError as exc:
            return (), str(exc)
        target_gross = sum(target_weights.values()) + sum(preserved_weights.values())
        if context is not None:
            protected_gross = sum(preserved_weights.values())
            budget = max(
                0.0, min(sum(requested.values()), self._limits.max_gross_exposure) - protected_gross
            )
            self._factor_plan_summaries[str(event.id)] = (
                f"risk passed; requested={sum(requested.values()):.2%}; "
                f"preserved={protected_gross:.2%}; normal_budget={budget:.2%}; "
                f"normal_target={sum(target_weights.values()):.2%}"
            )
        new_positions = self._daily_new_positions.get(
            self._date_key(self.clock.timestamp_ns()), 0
        ) + sum(
            1
            for instrument_id, weight in target_weights.items()
            if weight > 0 and current_quantities.get(instrument_id, 0) == 0
        )
        planned: list[_PlannedOrder] = []
        for canonical_id in sorted(required_ids):
            if canonical_id in preserved:
                continue
            execution_id = self._settings.instrument_routes[canonical_id]
            price = prices[canonical_id]
            weight = target_weights.get(canonical_id, 0.0)
            target_quantity = floor(equity * weight / price)
            delta = target_quantity - current_quantities[canonical_id]
            if delta == 0:
                continue
            notional = abs(delta) * price
            rejection = order_rejection_reason(
                notional_usd=notional,
                target_weight=weight,
                target_gross_exposure=target_gross,
                daily_new_positions=new_positions,
                limits=self._limits,
            )
            if rejection is not None:
                return (), f"{canonical_id}: {rejection}"
            planned.append(
                _PlannedOrder(
                    canonical_id=canonical_id,
                    instrument_id=execution_id,
                    side=OrderSide.BUY if delta > 0 else OrderSide.SELL,
                    quantity=abs(delta),
                    price=price,
                    target_weight=weight,
                    opens_position=(delta > 0 and current_quantities[canonical_id] == 0),
                )
            )
        planned.sort(key=lambda item: (item.side != OrderSide.SELL, item.canonical_id))
        return tuple(planned), None

    def _portfolio_equity(
        self,
        prices: dict[str, float],
        current_quantities: dict[str, int],
    ) -> float:
        currency = Currency.from_str("USD")
        account = self.portfolio.account(account_id=AccountId(self._settings.account_id))
        if account is None:
            return 0.0
        cash = account.balance_total(currency).as_double()
        positions_value = sum(
            quantity * prices[canonical_id]
            for canonical_id, quantity in current_quantities.items()
            if quantity != 0
        )
        if account.is_cash_account:
            account_equity = float(cash + positions_value)
        elif account.is_margin_account:
            # IBKR 适配器的 balance_total 已是 NetLiquidation, 不能重复加持仓。
            account_equity = float(cash)
        else:
            return 0.0
        return effective_strategy_equity(
            account_equity_usd=account_equity,
            strategy_capital_usd=self._settings.strategy_capital_usd,
        )

    def _current_quantity(self, instrument_id: InstrumentId) -> int:
        positions = self.cache.positions_open(
            instrument_id=instrument_id,
            account_id=AccountId(self._settings.account_id),
        )
        return round(sum(float(position.signed_qty) for position in positions))

    def _execute_plan(self, event: TradeSignalEvent, plan: tuple[_PlannedOrder, ...]) -> None:
        if event.factor_context is not None:
            timer = f"factor-expiry-{event.id}"
            if timer not in self.clock.timer_names:
                self.clock.set_time_alert_ns(
                    timer, event.expires_at_ns, callback=self._poll_approved_signal
                )
        signal_id = str(event.id)
        sells = tuple(item for item in plan if item.side == OrderSide.SELL)
        buys = tuple(item for item in plan if item.side == OrderSide.BUY)
        if not sells:
            self._submit_orders(event, buys)
            return
        self._pending_buys[signal_id] = (event, buys)
        self._open_sells[signal_id] = set()
        self._submit_orders(event, sells)

    def _submit_orders(
        self, event: TradeSignalEvent, planned_orders: tuple[_PlannedOrder, ...]
    ) -> None:
        prepared: list[tuple[Order, _PlannedOrder, _OrderContext]] = []
        for planned in planned_orders:
            rejection = self._account_rejection() or self._factor_rejection(event, execution=True)
            if rejection is not None:
                self._record_factor_skip(event, rejection)
                return
            instrument_id = InstrumentId.from_str(planned.instrument_id)
            instrument = self.cache.instrument(instrument_id)
            if instrument is None:
                raise RuntimeError(f"Instrument not found in cache: {instrument_id}")
            order = self.order_factory.market(
                instrument_id=instrument_id,
                order_side=planned.side,
                quantity=instrument.make_qty(planned.quantity, round_down=True),
                time_in_force=TimeInForce.DAY,
                reduce_only=planned.side == OrderSide.SELL,
                tags=[f"signal_event_id={event.id}"],
            )
            client_order_id = str(order.client_order_id)
            context = _OrderContext(
                signal_event_id=str(event.id),
                strategy_name=event.strategy_name,
                direction="BUY" if planned.side == OrderSide.BUY else "SELL",
                quantity=float(planned.quantity),
                is_sell=planned.side == OrderSide.SELL,
                signal_reason=event.reason,
                signal_timestamp_ns=event.ts_event,
                instrument_id=planned.instrument_id,
            )
            self._order_contexts[client_order_id] = context
            if context.is_sell:
                self._open_sells[context.signal_event_id].add(client_order_id)
            prepared.append((order, planned, context))
        # 先登记整组卖单, 同步成交回调也不能在第一笔卖出后提前补买。
        for index, (order, planned, context) in enumerate(prepared):
            if str(event.id) in self._failed_signals:
                return
            rejection = self._account_rejection() or self._factor_rejection(event, execution=True)
            if rejection is not None:
                self._record_factor_skip(event, rejection)
                self._pending_buys.pop(str(event.id), None)
                self._failed_signals.add(str(event.id))
                pending_sells = self._open_sells.get(str(event.id), set())
                for pending_order, _, _ in prepared[index:]:
                    pending_sells.discard(str(pending_order.client_order_id))
                return
            self._require_repository().record_order_event(
                signal_event_id=context.signal_event_id,
                order_event_id=None,
                timestamp_ns=max(self.clock.timestamp_ns(), event.ts_event),
                strategy_name=context.strategy_name,
                instrument_id=planned.instrument_id,
                client_order_id=str(order.client_order_id),
                status="CREATED",
                direction=context.direction,
                quantity=context.quantity,
                reason=event.reason,
            )
            self.submit_order(order)

    def on_order_submitted(self, event: OrderSubmitted) -> None:
        """记录 NT 已提交事件。"""
        self._record_order_status(event, status="SUBMITTED", reason="submitted")
        self._restore_daily_count()

    def on_order_accepted(self, event: OrderAccepted) -> None:
        """保存券商接收及其原生订单标识。"""
        self._record_order_status(event, status="ACCEPTED", reason="accepted")

    def on_order_expired(self, event: OrderExpired) -> None:
        """过期终态停止当前调仓续买。"""
        self._record_order_status(event, status="EXPIRED", reason="expired")
        context = self._order_contexts.get(str(event.client_order_id))
        if context is not None:
            self._finish_sell(context, str(event.client_order_id), failed=True)

    def on_order_filled(self, event: OrderFilled) -> None:
        """记录 NT 成交并在全部卖单完成后提交买单。"""
        # NT 可能推断补齐成交; 重启核对只接纳原始 FillReport。
        if event.reconciliation and self._settings.backtest_run_id is None:
            return
        self._accept_fill(event)

    def _accept_fill(self, event: OrderFilled | FillReport) -> None:
        if event.client_order_id is None:
            return
        context = self._restore_order_context(str(event.client_order_id))
        if context is None:
            return
        if (
            str(event.account_id) != self._settings.account_id
            or str(event.instrument_id) != context.instrument_id
            or event.order_side.name != context.direction
        ):
            self._recovery_error = "owned fill account mismatch"
            return
        order = self.cache.order(event.client_order_id)
        status = (
            "FILLED"
            if order is not None and order.status == OrderStatus.FILLED
            else "PARTIALLY_FILLED"
        )
        try:
            inserted = self._require_repository().record_fill(
                run_id=self._settings.backtest_run_id,
                signal_event_id=context.signal_event_id,
                trade_id=str(event.trade_id),
                timestamp_ns=max(event.ts_event, context.signal_timestamp_ns),
                strategy_name=context.strategy_name,
                instrument_id=str(event.instrument_id),
                client_order_id=str(event.client_order_id),
                direction=context.direction,
                quantity=event.last_qty.as_double(),
                price=event.last_px.as_double(),
                commission=event.commission.as_double(),
                order_event=OrderEventRecord(
                    event_id=context.signal_event_id,
                    order_event_id=str(event.id),
                    timestamp_utc=utc_datetime_from_ns(event.ts_event),
                    strategy_name=context.strategy_name,
                    instrument_id=str(event.instrument_id),
                    client_order_id=str(event.client_order_id),
                    status=status,
                    venue_order_id=str(event.venue_order_id),
                    direction=context.direction,
                    quantity=context.quantity,
                    reason=f"{context.signal_reason}; {status.lower()}",
                ),
            )
        except (ValueError, SQLAlchemyError) as exc:
            self._recovery_error = f"fill audit requires reconciliation: {type(exc).__name__}"
            self.log.error(self._recovery_error)
            return
        if inserted and order is not None and order.is_closed:
            self._finish_sell(context, str(event.client_order_id), failed=False)

    def _restore_order_context(self, client_id: str) -> _OrderContext | None:
        if client_id in self._order_contexts:
            return self._order_contexts[client_id]
        rows = self._require_repository().list_order_audits(
            scope=self._settings.signal_scope, client_order_id=client_id, limit=1
        )
        if not rows:
            return None
        row = rows[0]
        workflow = self._require_repository().get_signal_workflow(row.event_id)
        if workflow is None:
            return None
        if self._settings.model_release_id is not None and workflow.strategy_name != "patchtst_e3":
            return None
        context = _OrderContext(
            row.event_id,
            workflow.strategy_name,
            row.direction,
            row.quantity,
            row.direction == "SELL",
            workflow.reason,
            workflow.to_event().ts_event,
            row.instrument_id,
        )
        self._order_contexts[client_id] = context
        return context

    def _on_execution_report(self, report: object) -> None:
        """用持久化 client_order_id 认领报告, 不按证券接管手工订单。"""
        if self._repository is None:
            self._startup_reports.append(report)
            return
        if isinstance(report, ExecutionMassStatus):
            for fills in report.fill_reports.values():
                for fill in fills:
                    self._on_execution_report(fill)
            for order in report.order_reports.values():
                self._on_execution_report(order)
        elif isinstance(report, FillReport):
            self._accept_fill(report)
        elif isinstance(report, OrderStatusReport) and report.client_order_id is not None:
            context = self._restore_order_context(str(report.client_order_id))
            if context is None:
                return
            if (
                str(report.account_id) != self._settings.account_id
                or str(report.instrument_id) != context.instrument_id
                or report.order_side.name != context.direction
                or report.quantity.as_double() != context.quantity
            ):
                self._recovery_error = "owned order report identity or quantity mismatch"
                return
            self._require_repository().record_order_event(
                signal_event_id=context.signal_event_id,
                order_event_id=str(report.id),
                timestamp_ns=max(report.ts_last, context.signal_timestamp_ns),
                strategy_name=context.strategy_name,
                instrument_id=str(report.instrument_id),
                client_order_id=str(report.client_order_id),
                venue_order_id=str(report.venue_order_id),
                status=report.order_status.name,
                direction=context.direction,
                quantity=context.quantity,
                reason="broker reconciliation report",
            )

    def _on_external_order_event(self, event: object) -> None:
        """NT 重建为 EXTERNAL 的本策略订单仍按持久化归属接纳后续回报。"""
        if self._repository is None or getattr(event, "strategy_id", self.id) == self.id:
            return
        client_id = getattr(event, "client_order_id", None)
        if client_id is None or self._restore_order_context(str(client_id)) is None:
            return
        if isinstance(event, OrderFilled):
            self.on_order_filled(event)
        elif isinstance(event, OrderCanceled):
            self.on_order_canceled(event)
        elif isinstance(event, OrderExpired):
            self.on_order_expired(event)
        elif isinstance(event, OrderRejected):
            self.on_order_rejected(event)
        elif isinstance(event, OrderAccepted):
            self.on_order_accepted(event)

    def _restore_daily_count(self) -> None:
        now_ns = self.clock.timestamp_ns()
        self._daily_new_positions[self._date_key(now_ns)] = (
            self._require_repository().daily_new_position_count(
                scope=self._settings.signal_scope, timestamp_ns=now_ns
            )
        )

    def _ownership_rejection(self, current: dict[str, int]) -> str | None:
        """只有可由本作用域真实成交解释的数量才可继续调仓。"""
        repository = self._require_repository()
        routes = set(self._settings.instrument_routes.values())
        if any(str(order.instrument_id) in routes for order in self.cache.orders_open()):
            return "open broker orders require reconciliation before rebalance"
        owned: dict[str, float] = {}
        filled_by_order: dict[str, float] = {}
        offset = 0
        while True:
            fills = repository.list_fill_audits(
                scope=self._settings.signal_scope, offset=offset, limit=500
            )
            for fill in fills:
                if fill.strategy_name != "patchtst_e3":
                    continue
                filled_by_order[fill.client_order_id] = (
                    filled_by_order.get(fill.client_order_id, 0) + fill.quantity
                )
                owned[fill.instrument_id] = owned.get(fill.instrument_id, 0) + (
                    fill.quantity if fill.direction == "BUY" else -fill.quantity
                )
            if len(fills) < 500:
                break
            offset += 500
        for canonical, route in self._settings.instrument_routes.items():
            positions = self.cache.positions_open(
                instrument_id=InstrumentId.from_str(route),
                account_id=AccountId(self._settings.account_id),
            )
            actual = sum(float(position.signed_qty) for position in positions)
            if actual != current[canonical] or actual != owned.get(route, 0) or actual < 0:
                return (
                    f"position ownership or corporate action reconciliation required: {canonical}"
                )
        # CREATED 或提交中断不能因券商暂时没返回就当作从未提交。
        offset = 0
        while True:
            orders = repository.list_latest_order_audits(
                scope=self._settings.signal_scope, offset=offset, limit=500
            )
            for order in orders:
                native = self.cache.order(ClientOrderId(order.client_order_id))
                if order.status not in {"FILLED", "CANCELED", "EXPIRED", "REJECTED", "DENIED"}:
                    return "unresolved durable order requires reconciliation"
                expected_filled = (
                    order.quantity
                    if order.status == "FILLED"
                    else None
                    if native is None
                    else native.filled_qty.as_double()
                )
                if (
                    expected_filled is not None
                    and filled_by_order.get(order.client_order_id, 0) != expected_filled
                ):
                    return "broker fills missing from durable audit"
                if native is not None and not native.is_closed:
                    return "broker order is still open"
            if len(orders) < 500:
                break
            offset += 500
        return None

    def on_order_rejected(self, event: OrderRejected) -> None:
        """记录交易场所拒单。"""
        self._record_order_status(event, status="REJECTED", reason=event.reason)
        context = self._order_contexts.get(str(event.client_order_id))
        if context is not None:
            self._finish_sell(context, str(event.client_order_id), failed=True)

    def on_order_denied(self, event: OrderDenied) -> None:
        """记录 NT RiskEngine 拒单。"""
        self._record_order_status(event, status="DENIED", reason=event.reason)
        context = self._order_contexts.get(str(event.client_order_id))
        if context is not None:
            self._finish_sell(context, str(event.client_order_id), failed=True)

    def on_order_canceled(self, event: OrderCanceled) -> None:
        """撤单终态必须停止旧卖出流程的后续买入。"""
        self._record_order_status(event, status="CANCELED", reason="canceled")
        context = self._order_contexts.get(str(event.client_order_id))
        if context is not None:
            self._finish_sell(context, str(event.client_order_id), failed=True)

    def on_order_cancel_rejected(self, event: OrderCancelRejected) -> None:
        """撤单失败仍保持原单待核对, 不恢复后续买入。"""
        self._record_order_status(event, status="CANCEL_REJECTED", reason=event.reason)
        context = self._order_contexts.get(str(event.client_order_id))
        if context is None:
            return
        workflow = self._require_repository().get_signal_workflow(context.signal_event_id)
        if workflow is not None and workflow.factor_context is not None:
            self._pending_buys.pop(context.signal_event_id, None)
            self._failed_signals.add(context.signal_event_id)
            self._record_factor_skip(workflow.to_event(), "order cancellation rejected")

    def _record_order_status(
        self,
        event: OrderSubmitted
        | OrderAccepted
        | OrderExpired
        | OrderFilled
        | OrderRejected
        | OrderDenied
        | OrderCanceled
        | OrderCancelRejected,
        *,
        status: str,
        reason: str,
    ) -> None:
        context = self._order_contexts.get(str(event.client_order_id))
        if context is None:
            return
        self._require_repository().record_order_event(
            signal_event_id=context.signal_event_id,
            order_event_id=str(event.id),
            timestamp_ns=event.ts_event,
            strategy_name=context.strategy_name,
            instrument_id=str(event.instrument_id),
            client_order_id=str(event.client_order_id),
            venue_order_id=(
                None
                if getattr(event, "venue_order_id", None) is None
                else str(event.venue_order_id)
            ),
            status=status,
            direction=context.direction,
            quantity=context.quantity,
            reason=f"{context.signal_reason}; {reason}",
        )

    def _finish_sell(self, context: _OrderContext, client_order_id: str, *, failed: bool) -> None:
        signal_id = context.signal_event_id
        if failed:
            self._failed_signals.add(signal_id)
            if not context.is_sell:
                workflow = self._require_repository().get_signal_workflow(signal_id)
                if workflow is not None:
                    self._record_factor_skip(
                        workflow.to_event(), "buy order failed; stop remaining buys"
                    )
                for order in self.cache.orders_open():
                    other = self._order_contexts.get(str(order.client_order_id))
                    if (
                        other is not None
                        and other.signal_event_id == signal_id
                        and not order.is_pending_cancel
                    ):
                        self.cancel_order(order)
        if not context.is_sell:
            return
        open_sells = self._open_sells.get(signal_id)
        if open_sells is None:
            return
        open_sells.discard(client_order_id)
        if open_sells:
            return
        self._open_sells.pop(signal_id, None)
        pending = self._pending_buys.pop(signal_id, None)
        if signal_id in self._failed_signals:
            self._failed_signals.discard(signal_id)
            return
        if pending is None:
            return
        event, buys = pending
        if event.factor_context is not None:
            plan, rejection = self._build_plan(event)
            rejection = rejection or self._factor_rejection(event, execution=True)
            if rejection is not None:
                self._record_factor_skip(event, rejection)
                return
            # 重新计算只能继续尚未执行的买入, 不重放已经完成的卖单。
            buys = tuple(item for item in plan if item.side == OrderSide.BUY)
        self._submit_orders(event, buys)

    def _factor_rejection(self, event: TradeSignalEvent, *, execution: bool) -> str | None:
        """审批和提交边界复核同一因子依据, 不从最近批次补造旧上下文。"""
        context = event.factor_context
        if context is None:
            return (
                "missing factor context"
                if event.strategy_name == "patchtst_e3"
                or self._settings.model_release_id is not None
                else None
            )
        now_ns = self.clock.timestamp_ns()
        now = datetime.fromtimestamp(now_ns / 1_000_000_000, tz=UTC)
        if context.calendar_version != CALENDAR_VERSION:
            return "factor calendar mismatch"
        if (
            not self._settings.model_release_id
            or context.model_release_id != self._settings.model_release_id
        ):
            return "factor model release mismatch"
        if context.asof_date != expected_factor_date(now).isoformat():
            return "factor date is not current expected date"
        if event.ts_init > now_ns or now_ns >= event.expires_at_ns:
            return "factor signal unavailable or expired"
        opening, closing = factor_execution_window(date.fromisoformat(context.asof_date), 24)
        if event.not_before_ns != int(opening.timestamp() * 1e9) or not (
            event.not_before_ns < event.expires_at_ns <= int(closing.timestamp() * 1e9)
        ):
            return "invalid factor execution window"
        if execution and now_ns < event.not_before_ns:
            return "factor execution has not opened"
        if self._settings.backtest_run_id is not None and (
            execution or now_ns >= event.not_before_ns + 1_000
        ):
            traded_ids = {key for key, _ in event.target_weights} | {
                key
                for key, route in self._settings.instrument_routes.items()
                if self._current_quantity(InstrumentId.from_str(route)) != 0
                and key not in event.preserve_positions
            }
            for canonical_id in traded_ids:
                if canonical_id not in self._settings.instrument_routes:
                    return f"unknown factor target: {canonical_id}"
                quote = self.cache.quote_tick(
                    InstrumentId.from_str(self._settings.instrument_routes[canonical_id])
                )
                if quote is None or not (event.not_before_ns <= quote.ts_event <= now_ns):
                    return f"missing current-session opening quote: {canonical_id}"
        candidates = set(context.candidate_ids)
        preserve = set(event.preserve_positions)
        if (
            not candidates
            or len(candidates) != len(context.candidate_ids)
            or not (0 <= context.eligible_count <= len(candidates))
            or not preserve <= candidates
            or len(preserve) != len(candidates) - context.eligible_count
        ):
            return "invalid factor protection context"
        if len(preserve) / len(candidates) > self._limits.max_factor_unscorable_fraction:
            return "factor unscorable fraction exceeds risk limit"
        if not event.target_weights or any(
            key not in candidates or key in preserve or not isfinite(weight) or weight <= 0
            for key, weight in event.target_weights
        ):
            return "invalid factor targets"
        if context.source_kind not in {"signal_inference", "evaluation_predictions"}:
            return "invalid factor source kind"
        repository = self._require_repository()
        if self._settings.backtest_run_id is None:
            receipt = repository.get_factor_import(
                catalog_path=context.catalog_path, delivery_id=context.delivery_id, mode="paper"
            )
            if (
                context.source_kind != "signal_inference"
                or receipt is None
                or (
                    receipt.verified_at >= opening
                    or receipt.calendar_version != CALENDAR_VERSION
                    or receipt.model_release_id != context.model_release_id
                )
            ):
                return "missing timely paper factor acceptance"
        states = repository.list_factor_decisions(
            scope=self._settings.signal_scope,
            strategy_name=event.strategy_name,
            asof_date=context.asof_date,
        )
        if any(state.status == "SKIP" and state.recovered_at is None for state in states):
            return "factor decision is SKIP"
        if states:
            latest = states[0]
            if latest.context is not None and (
                latest.context.delivery_id != context.delivery_id
                or latest.preserve_positions != event.preserve_positions
            ):
                return "factor decision changed after approval"
        return None

    def _record_factor_skip(self, event: TradeSignalEvent, reason: str) -> None:
        if event.factor_context is None and event.strategy_name != "patchtst_e3":
            return
        now_ns = self.clock.timestamp_ns()
        context = event.factor_context
        asof_date = (
            context.asof_date
            if context is not None
            else expected_factor_date(
                datetime.fromtimestamp(now_ns / 1_000_000_000, tz=UTC)
            ).isoformat()
        )
        self._require_repository().record_factor_decision(
            scope=self._settings.signal_scope,
            strategy_name=event.strategy_name,
            asof_date=asof_date,
            status="SKIP",
            reason=f"execution:{reason}",
            timestamp_ns=now_ns,
            preserve_positions=event.preserve_positions,
            context=context,
        )

    def _reconcile_factor_orders(self) -> None:
        """停止失效的本策略延续步骤并撤销可证明归属的挂单。"""
        if self._settings.model_release_id is None:
            return
        for signal_id, (event, _) in tuple(self._pending_buys.items()):
            if self._factor_rejection(event, execution=True) is not None:
                self._pending_buys.pop(signal_id, None)
                self._failed_signals.add(signal_id)
        repository = self._require_repository()
        self._restore_daily_count()
        for order in self.cache.orders_open():
            client_id = str(order.client_order_id)
            restored = client_id not in self._order_contexts
            context = self._restore_order_context(client_id)
            if context is None:
                continue
            event_id = context.signal_event_id
            workflow = repository.get_signal_workflow(event_id)
            if workflow is None:
                continue
            event = workflow.to_event()
            if event.factor_context is None and event.strategy_name != "patchtst_e3":
                continue
            reason = (
                "restart reconciliation required"
                if restored
                else self._factor_rejection(event, execution=True)
            )
            if reason is not None:
                self._pending_buys.pop(event_id, None)
                self._failed_signals.add(event_id)
                self._record_factor_skip(event, reason)
                if not order.is_pending_cancel:
                    self.cancel_order(order)

    @staticmethod
    def _date_key(timestamp_ns: int) -> str:
        return datetime.fromtimestamp(timestamp_ns / 1_000_000_000, tz=UTC).date().isoformat()

    def _require_repository(self) -> TradingRepository:
        if self._repository is None:
            raise RuntimeError("Execution repository is not initialized")
        return self._repository

    def on_reset(self) -> None:
        """清空执行批次状态。"""
        self._order_contexts.clear()
        self._pending_buys.clear()
        self._open_sells.clear()
        self._failed_signals.clear()
        self._daily_new_positions.clear()
        self._execution_bar_requests.clear()
        self._deferred_signals.clear()
        self._execution_prices_ready = not self._settings.bootstrap_from_catalog
        self._factor_plan_summaries.clear()
        self._execution_price_date = None
        self._execution_request_started_ns = None
        self._execution_request_generation += 1
        self._startup_reports.clear()
        self._recovery_error = None

    def on_stop(self) -> None:
        """取消订阅并释放数据库连接。"""
        self.msgbus.unsubscribe(self._settings.signal_topic, self._handle_signal)
        if self._broker_status is not None:
            self.msgbus.unsubscribe("reports.execution.*", self._on_execution_report)
            self.msgbus.unsubscribe("events.order.*", self._on_external_order_event)
        if "approval-poll" in self.clock.timer_names:
            self.clock.cancel_timer("approval-poll")
        if self._repository is not None:
            self._repository.close()
            self._repository = None

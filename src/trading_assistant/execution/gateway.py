"""唯一允许提交 NT 订单的执行 Strategy。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import floor
from typing import Any, Literal

from nautilus_trader.common.component import TimeEvent
from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.events import OrderDenied, OrderFilled, OrderRejected, OrderSubmitted
from nautilus_trader.model.identifiers import AccountId, InstrumentId
from nautilus_trader.model.objects import Currency
from nautilus_trader.trading.strategy import Strategy

from trading_assistant.execution.events import TRADE_SIGNAL_TOPIC, TradeSignalEvent
from trading_assistant.risk.checks import (
    apply_weight_limits,
    effective_strategy_equity,
    order_rejection_reason,
)
from trading_assistant.risk.config import RiskLimits
from trading_assistant.storage.repository import TradingRepository

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

    instrument_ids: tuple[str, ...]
    bar_type_suffix: str
    approval_mode: str
    database_url: str
    strategy_capital_usd: float
    max_order_notional_usd: float
    max_instrument_weight: float
    max_daily_new_positions: int
    max_gross_exposure: float
    cash_adjustment_usd: float = 0.0
    backtest_run_id: str | None = None
    signal_topic: str = TRADE_SIGNAL_TOPIC
    approval_poll_interval_seconds: int = 5
    account_id: str | None = None


@dataclass(frozen=True)
class _PlannedOrder:
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
        )
        self._repository: TradingRepository | None = None
        self._order_contexts: dict[str, _OrderContext] = {}
        self._pending_buys: dict[str, tuple[TradeSignalEvent, tuple[_PlannedOrder, ...]]] = {}
        self._open_sells: dict[str, set[str]] = {}
        self._failed_sell_signals: set[str] = set()
        self._daily_new_positions: dict[str, int] = {}

    def on_start(self) -> None:
        """建立审计仓储并订阅领域事件。"""
        self._repository = TradingRepository(self._settings.database_url)
        self._repository.create_schema()
        self.msgbus.subscribe(self._settings.signal_topic, self._handle_signal)
        if self._settings.approval_mode == "manual":
            self.clock.set_timer(
                "approval-poll",
                interval=timedelta(seconds=self._settings.approval_poll_interval_seconds),
                callback=self._poll_approved_signal,
            )

    def _handle_signal(self, message: object) -> None:
        if not isinstance(message, TradeSignalEvent):
            return
        now_ns = max(self.clock.timestamp_ns(), message.ts_event)
        repository = self._require_repository()
        workflow = repository.get_signal_workflow(str(message.id))
        if workflow is None:
            self.log.error(f"Signal workflow not found: event_id={message.id}")
            return
        if workflow.status != "NEW":
            self.log.info(
                f"Signal workflow already handled: event_id={message.id}, status={workflow.status}"
            )
            return
        plan, rejection = self._build_plan(message)
        decision = gateway_decision(
            approval_mode=self._settings.approval_mode,
            expired=now_ns > message.expires_at_ns,
            risk_rejection=rejection,
        )
        reason = {
            "EXPIRED": "signal expired",
            "RISK_REJECTED": rejection or "risk rejected",
            "PENDING": "manual approval required",
            "APPROVED": "auto approval",
        }[decision]
        transitioned = False
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
                risk_summary="initial risk check passed",
                timestamp_ns=now_ns,
            )
        elif repository.claim_auto_signal(str(message.id), timestamp_ns=now_ns):
            transitioned = True
            self._record_opened_positions(plan, now_ns)
            self._execute_plan(message, plan)
            repository.finish_processing(
                str(message.id),
                status="ORDERS_SUBMITTED",
                timestamp_ns=now_ns,
            )
        if transitioned:
            repository.record_approval(
                message,
                approval_mode=self._settings.approval_mode,
                decision=decision,
                reason=f"{message.reason}; {reason}",
                timestamp_ns=now_ns,
            )

    def _poll_approved_signal(self, _: TimeEvent) -> None:
        """领取人工确认信号并在提交前重新计算和风控。"""
        repository = self._require_repository()
        while True:
            now_ns = self.clock.timestamp_ns()
            workflow = repository.claim_next_approved(timestamp_ns=now_ns)
            if workflow is None:
                return
            event = workflow.to_event()
            if now_ns > event.expires_at_ns:
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
            self._record_opened_positions(plan, now_ns)
            self._execute_plan(event, plan)
            repository.finish_processing(
                str(event.id),
                status="ORDERS_SUBMITTED",
                timestamp_ns=now_ns,
            )

    @staticmethod
    def _plan_payload(plan: tuple[_PlannedOrder, ...]) -> tuple[dict[str, Any], ...]:
        return tuple(
            {
                "instrument_id": item.instrument_id,
                "side": "BUY" if item.side == OrderSide.BUY else "SELL",
                "quantity": item.quantity,
                "price": item.price,
                "target_weight": item.target_weight,
            }
            for item in plan
        )

    def _record_opened_positions(self, plan: tuple[_PlannedOrder, ...], timestamp_ns: int) -> None:
        date_key = self._date_key(timestamp_ns)
        opened = sum(1 for item in plan if item.opens_position)
        self._daily_new_positions[date_key] = self._daily_new_positions.get(date_key, 0) + opened

    def _build_plan(self, event: TradeSignalEvent) -> tuple[tuple[_PlannedOrder, ...], str | None]:
        requested = dict(event.target_weights)
        target_weights = apply_weight_limits(requested, self._limits)
        prices: dict[str, float] = {}
        current_quantities: dict[str, int] = {}
        for value in self._settings.instrument_ids:
            bar = self.cache.bar(BarType.from_str(f"{value}-{self._settings.bar_type_suffix}"))
            if bar is None:
                return (), f"missing price for {value}"
            prices[value] = bar.close.as_double()
            current_quantities[value] = self._current_quantity(InstrumentId.from_str(value))

        equity = self._portfolio_equity(prices)
        if equity <= 0:
            return (), "non-positive portfolio equity"

        target_gross = sum(target_weights.values())
        new_positions = self._daily_new_positions.get(
            self._date_key(self.clock.timestamp_ns()), 0
        ) + sum(
            1
            for instrument_id, weight in target_weights.items()
            if weight > 0 and current_quantities.get(instrument_id, 0) == 0
        )
        planned: list[_PlannedOrder] = []
        for instrument_id in self._settings.instrument_ids:
            price = prices[instrument_id]
            weight = target_weights.get(instrument_id, 0.0)
            target_quantity = floor(equity * weight / price)
            delta = target_quantity - current_quantities[instrument_id]
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
                return (), f"{instrument_id}: {rejection}"
            planned.append(
                _PlannedOrder(
                    instrument_id=instrument_id,
                    side=OrderSide.BUY if delta > 0 else OrderSide.SELL,
                    quantity=abs(delta),
                    price=price,
                    target_weight=weight,
                    opens_position=(delta > 0 and current_quantities[instrument_id] == 0),
                )
            )
        planned.sort(key=lambda item: (item.side != OrderSide.SELL, item.instrument_id))
        return tuple(planned), None

    def _portfolio_equity(self, prices: dict[str, float]) -> float:
        currency = Currency.from_str("USD")
        accounts: dict[str, Any] = {}
        if self._settings.account_id is not None:
            account = self.portfolio.account(
                account_id=AccountId(self._settings.account_id),
            )
            if account is not None:
                accounts[str(account.id)] = account
        else:
            for value in self._settings.instrument_ids:
                instrument_id = InstrumentId.from_str(value)
                account = self.portfolio.account(venue=instrument_id.venue)
                if account is not None:
                    accounts[str(account.id)] = account
        cash = sum(account.balance_total(currency).as_double() for account in accounts.values())
        positions_value = sum(
            self._current_quantity(InstrumentId.from_str(value)) * prices[value]
            for value in self._settings.instrument_ids
        )
        account_equity = float(cash - self._settings.cash_adjustment_usd + positions_value)
        return effective_strategy_equity(
            account_equity_usd=account_equity,
            strategy_capital_usd=self._settings.strategy_capital_usd,
        )

    def _current_quantity(self, instrument_id: InstrumentId) -> int:
        account_id = (
            AccountId(self._settings.account_id) if self._settings.account_id is not None else None
        )
        positions = self.cache.positions_open(
            instrument_id=instrument_id,
            account_id=account_id,
        )
        return round(sum(float(position.signed_qty) for position in positions))

    def _execute_plan(self, event: TradeSignalEvent, plan: tuple[_PlannedOrder, ...]) -> None:
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
        for planned in planned_orders:
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
            )
            self._order_contexts[client_order_id] = context
            if context.is_sell:
                self._open_sells[context.signal_event_id].add(client_order_id)
            self._require_repository().record_order_event(
                signal_event_id=context.signal_event_id,
                order_event_id=None,
                timestamp_ns=max(self.clock.timestamp_ns(), event.ts_event),
                strategy_name=context.strategy_name,
                instrument_id=planned.instrument_id,
                client_order_id=client_order_id,
                status="CREATED",
                direction=context.direction,
                quantity=context.quantity,
                reason=event.reason,
            )
            self.submit_order(order)

    def on_order_submitted(self, event: OrderSubmitted) -> None:
        """记录 NT 已提交事件。"""
        self._record_order_status(event, status="SUBMITTED", reason="submitted")

    def on_order_filled(self, event: OrderFilled) -> None:
        """记录 NT 成交并在全部卖单完成后提交买单。"""
        context = self._order_contexts.get(str(event.client_order_id))
        if context is None:
            return
        self._record_order_status(event, status="FILLED", reason="filled")
        self._require_repository().record_fill(
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
        )
        order = self.cache.order(event.client_order_id)
        if order is not None and order.is_closed:
            self._finish_sell(context, str(event.client_order_id), failed=False)

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

    def _record_order_status(
        self,
        event: OrderSubmitted | OrderFilled | OrderRejected | OrderDenied,
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
            status=status,
            direction=context.direction,
            quantity=context.quantity,
            reason=f"{context.signal_reason}; {reason}",
        )

    def _finish_sell(self, context: _OrderContext, client_order_id: str, *, failed: bool) -> None:
        if not context.is_sell:
            return
        signal_id = context.signal_event_id
        if failed:
            self._failed_sell_signals.add(signal_id)
        open_sells = self._open_sells.get(signal_id)
        if open_sells is None:
            return
        open_sells.discard(client_order_id)
        if open_sells:
            return
        self._open_sells.pop(signal_id, None)
        pending = self._pending_buys.pop(signal_id, None)
        if signal_id in self._failed_sell_signals:
            self._failed_sell_signals.discard(signal_id)
            return
        if pending is None:
            return
        event, buys = pending
        self._submit_orders(event, buys)

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
        self._failed_sell_signals.clear()
        self._daily_new_positions.clear()

    def on_stop(self) -> None:
        """取消订阅并释放数据库连接。"""
        self.msgbus.unsubscribe(self._settings.signal_topic, self._handle_signal)
        if "approval-poll" in self.clock.timer_names:
            self.clock.cancel_timer("approval-poll")
        if self._repository is not None:
            self._repository.close()
            self._repository = None

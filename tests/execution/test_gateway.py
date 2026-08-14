"""统一执行网关决策与持久化审批链路测试。"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import cast

import pytest
from nautilus_trader.common.component import TestClock, TimeEvent
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import ClientId, InstrumentId

from tests.data.helpers import make_bar
from trading_assistant.execution.events import TradeSignalEvent
from trading_assistant.execution.gateway import (
    ExecutionGatewayConfig,
    ExecutionGatewayStrategy,
    _PlannedOrder,
    gateway_decision,
)
from trading_assistant.storage.repository import TradingRepository


class _GatewayHarness(ExecutionGatewayStrategy):
    def __init__(self, config: ExecutionGatewayConfig) -> None:
        super().__init__(config)
        self.rejection: str | None = None
        self.executed: list[str] = []
        self.test_clock = TestClock()
        self.test_clock.set_time(time.time_ns())

    @property
    def clock(self) -> TestClock:
        return self.test_clock

    def _build_plan(
        self,
        event: TradeSignalEvent,
    ) -> tuple[tuple[_PlannedOrder, ...], str | None]:
        return (), self.rejection

    def _execute_plan(
        self,
        event: TradeSignalEvent,
        plan: tuple[_PlannedOrder, ...],
    ) -> None:
        self.executed.append(str(event.id))


class _PlanningCache:
    """为计划计算提供最新执行 Bar。"""

    def __init__(
        self,
        bars: dict[str, Bar],
        positions: tuple[_Position, ...] = (),
    ) -> None:
        self._bars = bars
        self._positions = positions

    def bar(self, bar_type: BarType) -> Bar | None:
        return self._bars.get(str(bar_type))

    def instrument(self, instrument_id: InstrumentId) -> None:
        del instrument_id
        return None

    def positions_open(
        self,
        *,
        instrument_id: InstrumentId,
        account_id: object,
    ) -> list[_Position]:
        del instrument_id, account_id
        return list(self._positions)


class _Position:
    def __init__(self, signed_qty: float) -> None:
        self.signed_qty = signed_qty


class _PlanningGateway(ExecutionGatewayStrategy):
    """隔离账户外部依赖, 直接测试 canonical 到 live 的计划映射。"""

    def __init__(
        self,
        config: ExecutionGatewayConfig,
        *,
        equity: float,
        quantities: dict[str, int] | None = None,
        bars: dict[str, Bar] | None = None,
    ) -> None:
        super().__init__(config)
        self.test_clock = TestClock()
        self.test_clock.set_time(time.time_ns())
        self.test_cache = _PlanningCache(bars or {})
        self.equity = equity
        self.quantities = quantities or {}

    @property
    def clock(self) -> TestClock:
        return self.test_clock

    @property
    def cache(self) -> _PlanningCache:
        return self.test_cache

    def _portfolio_equity(
        self,
        prices: dict[str, float],
        current_quantities: dict[str, int],
    ) -> float:
        assert prices
        assert set(current_quantities) == set(self._settings.instrument_routes)
        return self.equity

    def _current_quantity(self, instrument_id: InstrumentId) -> int:
        return self.quantities.get(str(instrument_id), 0)


class _CatalogBootstrapGateway(_PlanningGateway):
    """同步返回 Catalog 请求以复现 live 启动时序。"""

    def __init__(
        self,
        config: ExecutionGatewayConfig,
        *,
        equity: float,
        bars: dict[str, Bar],
    ) -> None:
        super().__init__(config, equity=equity, bars=bars)
        self.requested_bar_types: list[tuple[str, str | None]] = []

    def request_bars(
        self,
        bar_type: BarType,
        start: datetime,
        end: datetime | None = None,
        limit: int = 0,
        client_id: ClientId | None = None,
        callback: Callable[[UUID4], None] | None = None,
        update_catalog: bool = False,
        join_request: bool = False,
        request_id: UUID4 | None = None,
        params: dict[str, object] | None = None,
    ) -> UUID4:
        del start, end, limit, update_catalog, join_request, params
        used_request_id = request_id or UUID4()
        self.requested_bar_types.append(
            (str(bar_type), None if client_id is None else str(client_id))
        )
        if callback is not None:
            callback(used_request_id)
        return used_request_id


def _planning_config(
    tmp_path: Path,
    *,
    instrument_routes: dict[str, str] | None = None,
    execution_bar_types: dict[str, str] | None = None,
    max_order_notional_usd: float = 100_000,
    approval_mode: str = "auto",
    bootstrap_from_catalog: bool = False,
    signal_scope: str = "default",
) -> ExecutionGatewayConfig:
    routes = instrument_routes or {"SPY.US": "SPY.ARCA"}
    return ExecutionGatewayConfig(
        instrument_routes=routes,
        execution_bar_types=(
            {canonical_id: f"{canonical_id}-1-DAY-LAST-EXTERNAL" for canonical_id in routes}
            if execution_bar_types is None
            else execution_bar_types
        ),
        approval_mode=approval_mode,
        database_url=f"sqlite:///{tmp_path}/planning.db",
        account_id="IB-DU123",
        strategy_capital_usd=10_000,
        max_order_notional_usd=max_order_notional_usd,
        max_instrument_weight=0.25,
        max_daily_new_positions=3,
        max_gross_exposure=0.8,
        signal_scope=signal_scope,
        bootstrap_from_catalog=bootstrap_from_catalog,
        catalog_lookback_days=10,
    )


def _planning_event(
    target_weights: tuple[tuple[str, float], ...] = (("SPY.US", 0.25),),
    *,
    rebalance_key: str = "2025-01",
    timestamp_ns: int | None = None,
    expiry_hours: int = 1,
) -> TradeSignalEvent:
    now_ns = time.time_ns() if timestamp_ns is None else timestamp_ns
    return TradeSignalEvent(
        strategy_name="dual_momentum",
        target_weights=target_weights,
        rebalance_key=rebalance_key,
        reason="momentum",
        expires_at_ns=now_ns + expiry_hours * 3_600_000_000_000,
        ts_event=now_ns,
        ts_init=now_ns,
    )


def _gateway(tmp_path: Path, *, approval_mode: str) -> tuple[_GatewayHarness, TradingRepository]:
    repository = TradingRepository(f"sqlite:///{tmp_path}/gateway.db")
    repository.create_schema()
    gateway = _GatewayHarness(
        ExecutionGatewayConfig(
            instrument_routes={"SPY.US": "SPY.ARCA"},
            execution_bar_types={"SPY.US": "SPY.US-1-DAY-LAST-EXTERNAL"},
            approval_mode=approval_mode,
            database_url=f"sqlite:///{tmp_path}/gateway.db",
            account_id="IB-DU123",
            strategy_capital_usd=10_000,
            max_order_notional_usd=100_000,
            max_instrument_weight=0.25,
            max_daily_new_positions=3,
            max_gross_exposure=0.8,
        )
    )
    gateway._repository = repository
    return gateway, repository


def _event(repository: TradingRepository, *, expires_at_ns: int | None = None) -> TradeSignalEvent:
    now_ns = time.time_ns()
    event = TradeSignalEvent(
        strategy_name="dual_momentum",
        target_weights=(("SPY.US", 0.25),),
        rebalance_key=str(now_ns),
        reason="momentum",
        expires_at_ns=expires_at_ns or now_ns + 3_600_000_000_000,
        ts_event=now_ns,
        ts_init=now_ns,
    )
    repository.register_signal_workflow(event, scope="paper:DU123")
    return event


def test_expired_and_risk_rejected_never_execute() -> None:
    assert gateway_decision(approval_mode="auto", expired=True, risk_rejection=None) == "EXPIRED"
    assert (
        gateway_decision(approval_mode="auto", expired=False, risk_rejection="risk")
        == "RISK_REJECTED"
    )


def test_manual_waits_and_auto_executes_after_risk() -> None:
    assert gateway_decision(approval_mode="manual", expired=False, risk_rejection=None) == "PENDING"
    assert gateway_decision(approval_mode="auto", expired=False, risk_rejection=None) == "APPROVED"


def test_unknown_approval_mode_fails_closed() -> None:
    with pytest.raises(ValueError, match="Unsupported approval mode"):
        gateway_decision(approval_mode="invalid", expired=False, risk_rejection=None)


def test_manual_signal_never_executes_before_approval(tmp_path: Path) -> None:
    gateway, repository = _gateway(tmp_path, approval_mode="manual")
    event = _event(repository)
    gateway._handle_signal(event)
    assert repository.get_signal_workflow(str(event.id)).status == "PENDING"  # type: ignore[union-attr]
    assert gateway.executed == []

    repository.decide_manual_approval(
        str(event.id),
        approved=True,
        decision_by="42",
        timestamp_ns=time.time_ns(),
    )
    gateway._poll_approved_signal(cast(TimeEvent, object()))
    assert repository.get_signal_workflow(str(event.id)).status == "ORDERS_SUBMITTED"  # type: ignore[union-attr]
    assert gateway.executed == [str(event.id)]
    gateway._poll_approved_signal(cast(TimeEvent, object()))
    assert gateway.executed == [str(event.id)]
    repository.close()


def test_manual_second_risk_check_can_reject(tmp_path: Path) -> None:
    gateway, repository = _gateway(tmp_path, approval_mode="manual")
    event = _event(repository)
    gateway._handle_signal(event)
    repository.decide_manual_approval(
        str(event.id),
        approved=True,
        decision_by="42",
        timestamp_ns=time.time_ns(),
    )
    gateway.rejection = "price moved beyond risk limit"
    gateway._poll_approved_signal(cast(TimeEvent, object()))
    assert repository.get_signal_workflow(str(event.id)).status == "RISK_REJECTED"  # type: ignore[union-attr]
    assert gateway.executed == []
    repository.close()


def test_denied_manual_signal_is_not_claimed(tmp_path: Path) -> None:
    gateway, repository = _gateway(tmp_path, approval_mode="manual")
    event = _event(repository)
    gateway._handle_signal(event)
    repository.decide_manual_approval(
        str(event.id),
        approved=False,
        decision_by="42",
        timestamp_ns=time.time_ns(),
    )
    gateway._poll_approved_signal(cast(TimeEvent, object()))
    assert repository.get_signal_workflow(str(event.id)).status == "DENIED"  # type: ignore[union-attr]
    assert gateway.executed == []
    repository.close()


def test_auto_claims_once_and_initial_failures_never_execute(tmp_path: Path) -> None:
    gateway, repository = _gateway(tmp_path, approval_mode="auto")
    event = _event(repository)
    gateway._handle_signal(event)
    gateway._handle_signal(event)
    assert repository.get_signal_workflow(str(event.id)).status == "ORDERS_SUBMITTED"  # type: ignore[union-attr]
    assert gateway.executed == [str(event.id)]

    rejected = _event(repository)
    gateway.rejection = "risk"
    gateway._handle_signal(rejected)
    assert repository.get_signal_workflow(str(rejected.id)).status == "RISK_REJECTED"  # type: ignore[union-attr]

    expired = _event(repository, expires_at_ns=time.time_ns() - 1)
    gateway.rejection = None
    gateway._handle_signal(expired)
    assert repository.get_signal_workflow(str(expired.id)).status == "EXPIRED"  # type: ignore[union-attr]
    assert gateway.executed == [str(event.id)]
    repository.close()


def test_catalog_bootstrap_defers_and_recovers_new_signals_by_scope(tmp_path: Path) -> None:
    """模拟 8 月 13 日 11:44 ET 启动。Gateway 先加载执行价再恢复 NEW 信号。"""
    routes = {"SPY.US": "SPY.ARCA", "QQQ.US": "QQQ.NASDAQ"}
    spy = make_bar(date(2026, 8, 12), instrument_id="SPY.US", close=100)
    qqq = make_bar(
        date(2026, 8, 12),
        instrument_id="QQQ.US",
        open_price=199,
        high=201,
        low=198,
        close=200,
    )
    bars = {str(spy.bar_type): spy, str(qqq.bar_type): qqq}
    gateway = _CatalogBootstrapGateway(
        _planning_config(
            tmp_path,
            instrument_routes=routes,
            approval_mode="manual",
            bootstrap_from_catalog=True,
            signal_scope="paper:DU123",
        ),
        equity=10_000,
        bars=bars,
    )
    repository = TradingRepository(f"sqlite:///{tmp_path}/planning.db")
    repository.create_schema()
    gateway._repository = repository

    signal_time_ns = int(datetime(2026, 8, 13, tzinfo=UTC).timestamp() * 1_000_000_000)
    simulated_intraday_ns = int(
        datetime(2026, 8, 13, 15, 44, tzinfo=UTC).timestamp() * 1_000_000_000
    )
    gateway.test_clock.set_time(simulated_intraday_ns)

    deferred = _planning_event(
        rebalance_key="2026-08-13-deferred",
        timestamp_ns=signal_time_ns,
        expiry_hours=24,
    )
    recovered = _planning_event(
        (("QQQ.US", 0.25),),
        rebalance_key="2026-08-13-recovered",
        timestamp_ns=signal_time_ns,
        expiry_hours=24,
    )
    other_scope = _planning_event(
        rebalance_key="2026-08-13-other",
        timestamp_ns=signal_time_ns,
        expiry_hours=24,
    )
    repository.register_signal_workflow(deferred, scope="paper:DU123")
    repository.register_signal_workflow(recovered, scope="paper:DU123")
    repository.register_signal_workflow(other_scope, scope="paper:OTHER")

    gateway._handle_signal(deferred)
    assert repository.get_signal_workflow(str(deferred.id)).status == "NEW"  # type: ignore[union-attr]

    gateway._request_execution_bar_history()

    assert set(gateway.requested_bar_types) == {
        ("SPY.US-1-DAY-LAST-EXTERNAL", "CATALOG"),
        ("QQQ.US-1-DAY-LAST-EXTERNAL", "CATALOG"),
    }
    assert gateway._execution_prices_ready is True
    assert gateway._deferred_signals == {}
    assert repository.get_signal_workflow(str(deferred.id)).status == "PENDING"  # type: ignore[union-attr]
    assert repository.get_signal_workflow(str(recovered.id)).status == "PENDING"  # type: ignore[union-attr]
    assert repository.get_signal_workflow(str(other_scope.id)).status == "NEW"  # type: ignore[union-attr]
    repository.close()


def test_plan_uses_canonical_prices_and_routes_live_order(tmp_path: Path) -> None:
    bar = make_bar(
        date(2025, 1, 2),
        instrument_id="SPY.US",
        close=100,
    )
    gateway = _PlanningGateway(
        _planning_config(tmp_path),
        equity=10_000,
        bars={str(bar.bar_type): bar},
    )

    plan, rejection = gateway._build_plan(_planning_event())

    assert rejection is None
    assert len(plan) == 1
    assert plan[0].canonical_id == "SPY.US"
    assert plan[0].instrument_id == "SPY.ARCA"
    assert plan[0].side == OrderSide.BUY
    assert plan[0].quantity == 25
    assert plan[0].opens_position
    assert gateway._plan_payload(plan)[0]["asset_id"] == "SPY.US"
    gateway._record_opened_positions(plan, gateway.clock.timestamp_ns())
    assert sum(gateway._daily_new_positions.values()) == 1


def test_plan_fails_closed_for_missing_inputs_and_risk(tmp_path: Path) -> None:
    event = _planning_event()
    missing_type = _PlanningGateway(
        _planning_config(tmp_path, execution_bar_types={}),
        equity=10_000,
    )
    assert missing_type._build_plan(event)[1] == "missing execution BarType for SPY.US"

    missing_price = _PlanningGateway(_planning_config(tmp_path), equity=10_000)
    assert missing_price._build_plan(event)[1] == "missing price for SPY.US"

    bar = make_bar(date(2025, 1, 2), instrument_id="SPY.US", close=100)
    bars = {str(bar.bar_type): bar}
    no_equity = _PlanningGateway(
        _planning_config(tmp_path),
        equity=0,
        bars=bars,
    )
    assert no_equity._build_plan(event)[1] == "non-positive portfolio equity"

    risk_rejected = _PlanningGateway(
        _planning_config(tmp_path, max_order_notional_usd=100),
        equity=10_000,
        bars=bars,
    )
    assert "max_order_notional_usd" in str(risk_rejected._build_plan(event)[1])

    unchanged = _PlanningGateway(
        _planning_config(tmp_path),
        equity=10_000,
        quantities={"SPY.ARCA": 25},
        bars=bars,
    )
    assert unchanged._build_plan(event) == ((), None)

    seller = _PlanningGateway(
        _planning_config(tmp_path),
        equity=10_000,
        quantities={"SPY.ARCA": 30},
        bars=bars,
    )
    plan, rejection = seller._build_plan(event)
    assert rejection is None
    assert plan[0].side == OrderSide.SELL
    assert plan[0].quantity == 5
    assert not plan[0].opens_position


def test_plan_only_requires_prices_for_targets_and_open_positions(tmp_path: Path) -> None:
    """空仓候选无需行情; 目标与待退出持仓仍必须有行情。"""
    routes = {"SPY.US": "SPY.ARCA", "QQQ.US": "QQQ.NASDAQ"}
    spy = make_bar(date(2025, 1, 2), instrument_id="SPY.US", close=100)
    gateway = _PlanningGateway(
        _planning_config(tmp_path, instrument_routes=routes),
        equity=10_000,
        bars={str(spy.bar_type): spy},
    )
    plan, rejection = gateway._build_plan(_planning_event())
    assert rejection is None
    assert [item.canonical_id for item in plan] == ["SPY.US"]

    missing_target = _planning_event((("QQQ.US", 0.25),))
    assert gateway._build_plan(missing_target)[1] == "missing price for QQQ.US"

    missing_exit = _PlanningGateway(
        _planning_config(tmp_path, instrument_routes=routes),
        equity=10_000,
        quantities={"QQQ.NASDAQ": 5},
        bars={str(spy.bar_type): spy},
    )
    assert missing_exit._build_plan(_planning_event())[1] == "missing price for QQQ.US"

    qqq = make_bar(
        date(2025, 1, 2),
        instrument_id="QQQ.US",
        open_price=199,
        high=201,
        low=198,
        close=200,
    )
    missing_exit.test_cache = _PlanningCache({str(spy.bar_type): spy, str(qqq.bar_type): qqq})
    plan, rejection = missing_exit._build_plan(_planning_event())
    assert rejection is None
    assert [item.side for item in plan] == [OrderSide.SELL, OrderSide.BUY]
    assert plan[0].canonical_id == "QQQ.US"
    assert plan[0].quantity == 5


def test_plan_rejects_unknown_targets_and_accepts_empty_portfolio(tmp_path: Path) -> None:
    gateway = _PlanningGateway(_planning_config(tmp_path), equity=0)
    unknown = _planning_event((("UNKNOWN.US", 0.25),))
    assert gateway._build_plan(unknown)[1] == "unknown target instruments: UNKNOWN.US"
    assert gateway._build_plan(_planning_event(())) == ((), None)


class _Money:
    def __init__(self, value: float) -> None:
        self._value = value

    def as_double(self) -> float:
        return self._value


class _Account:
    def balance_total(self, currency: object) -> _Money:
        del currency
        return _Money(5_000)


class _Portfolio:
    def __init__(self, account: _Account | None) -> None:
        self._account = account

    def account(self, *, account_id: object) -> _Account | None:
        del account_id
        return self._account


class _PortfolioPlanningGateway(_PlanningGateway):
    def __init__(
        self,
        config: ExecutionGatewayConfig,
        *,
        account: _Account | None,
        quantities: dict[str, int],
        bars: dict[str, Bar],
    ) -> None:
        super().__init__(
            config,
            equity=0,
            quantities=quantities,
            bars=bars,
        )
        self.test_portfolio = _Portfolio(account)

    @property
    def portfolio(self) -> _Portfolio:
        return self.test_portfolio

    def _portfolio_equity(
        self,
        prices: dict[str, float],
        current_quantities: dict[str, int],
    ) -> float:
        return ExecutionGatewayStrategy._portfolio_equity(self, prices, current_quantities)


def test_portfolio_equity_uses_exact_account_and_live_positions(tmp_path: Path) -> None:
    bar = make_bar(date(2025, 1, 2), instrument_id="SPY.US", close=100)
    bars = {str(bar.bar_type): bar}
    missing = _PortfolioPlanningGateway(
        _planning_config(tmp_path),
        account=None,
        quantities={},
        bars=bars,
    )
    assert missing._portfolio_equity({"SPY.US": 100}, {"SPY.US": 0}) == 0

    gateway = _PortfolioPlanningGateway(
        _planning_config(tmp_path),
        account=_Account(),
        quantities={"SPY.ARCA": 10},
        bars=bars,
    )
    assert gateway._portfolio_equity({"SPY.US": 100}, {"SPY.US": 10}) == 6_000
    assert gateway._current_quantity(InstrumentId.from_str("SPY.ARCA")) == 10
    gateway.test_cache = _PlanningCache(bars, (_Position(3), _Position(2)))
    assert (
        ExecutionGatewayStrategy._current_quantity(
            gateway,
            InstrumentId.from_str("SPY.ARCA"),
        )
        == 5
    )


def test_execute_plan_orders_sells_before_buys_and_missing_instrument_fails(
    tmp_path: Path,
) -> None:
    gateway = _PlanningGateway(_planning_config(tmp_path), equity=10_000)
    event = _planning_event()
    buy = _PlannedOrder("SPY.US", "SPY.ARCA", OrderSide.BUY, 1, 100, 0.25, True)
    sell = _PlannedOrder("QQQ.US", "QQQ.NASDAQ", OrderSide.SELL, 1, 100, 0, False)
    submitted: list[tuple[_PlannedOrder, ...]] = []
    gateway._submit_orders = lambda _event, plan: submitted.append(plan)  # type: ignore[assignment]

    gateway._execute_plan(event, (buy,))
    assert submitted == [(buy,)]
    gateway._execute_plan(event, (buy, sell))
    assert submitted[-1] == (sell,)
    assert gateway._pending_buys[str(event.id)][1] == (buy,)

    failing = _PlanningGateway(_planning_config(tmp_path), equity=10_000)
    with pytest.raises(RuntimeError, match="Instrument not found"):
        failing._submit_orders(event, (buy,))

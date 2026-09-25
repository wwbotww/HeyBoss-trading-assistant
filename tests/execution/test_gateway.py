"""统一执行网关决策与持久化审批链路测试。"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import cast

import pytest
from nautilus_trader.common.component import TestClock, TimeEvent
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import ClientId, InstrumentId, PositionId

from tests.data.helpers import make_bar
from trading_assistant.data.market_calendar import CALENDAR_VERSION
from trading_assistant.execution.events import FactorContext, TradeSignalEvent
from trading_assistant.execution.gateway import (
    ExecutionGatewayConfig,
    ExecutionGatewayStrategy,
    _PlannedOrder,
    gateway_decision,
)
from trading_assistant.storage.models import ApprovalRecord
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

    def orders_open(self, *, strategy_id: object = None) -> list[object]:
        del strategy_id
        return []

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
        del start, end, limit, update_catalog, join_request
        from trading_assistant.data.catalog import CatalogRequestOutcome

        assert params is not None
        outcome = params["catalog_outcome"]
        assert isinstance(outcome, CatalogRequestOutcome)
        outcome.status = "ok"
        outcome.rows_received = int(self.cache.bar(bar_type) is not None)
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
            signal_scope="paper:DU123",
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
    # 此替身同步完成请求; 新信号本身即可触发预热。

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
    is_cash_account = True
    is_margin_account = False

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
    gateway.test_portfolio._account.is_cash_account = False  # type: ignore[union-attr]
    gateway.test_portfolio._account.is_margin_account = True  # type: ignore[union-attr]
    assert gateway._portfolio_equity({"SPY.US": 100}, {"SPY.US": 10}) == 5_000
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


def _factor_setup(tmp_path: Path) -> tuple[_PlanningGateway, TradingRepository, TradeSignalEvent]:
    """完整十标的候选, 一个缺分持仓和一个有效低分持仓。"""
    ids = tuple(f"S{i}.US" for i in range(10))
    config = ExecutionGatewayConfig(
        instrument_routes={key: key for key in ids},
        execution_bar_types={key: f"{key}-1-DAY-LAST-EXTERNAL" for key in ids},
        approval_mode="manual",
        database_url=f"sqlite:///{tmp_path}/factor.db",
        account_id="IB-DU123",
        signal_scope="paper:factor",
        model_release_id="b" * 64,
        strategy_capital_usd=10_000,
        max_order_notional_usd=100_000,
        max_instrument_weight=0.25,
        max_daily_new_positions=3,
        max_gross_exposure=0.8,
        max_factor_unscorable_fraction=0.2,
        max_factor_preserved_price_age_sessions=1,
    )
    bars = {
        f"{key}-1-DAY-LAST-EXTERNAL": make_bar(
            date(2025, 1, 2),
            instrument_id=key,
            close=100,
        )
        for key in ids
    }
    gateway = _PlanningGateway(
        config, equity=10_000, quantities={"S9.US": 25, "S8.US": 10}, bars=bars
    )
    now = datetime(2025, 1, 3, 13, tzinfo=UTC)
    gateway.test_clock.set_time(int(now.timestamp() * 1e9))
    repository = TradingRepository(config.database_url)
    repository.create_schema()
    gateway._repository = repository
    context = FactorContext(
        "2025-01-02",
        "d" * 64,
        "b" * 64,
        "signal_inference",
        CALENDAR_VERSION,
        ids,
        9,
        str(tmp_path / "catalog"),
    )
    repository.record_factor_import(
        catalog_path=context.catalog_path,
        delivery_id=context.delivery_id,
        mode="paper",
        model_release_id=context.model_release_id,
        calendar_version=CALENDAR_VERSION,
        source_created_at=datetime(2025, 1, 3, 0, tzinfo=UTC),
        verified_at=now,
    )
    event = _protected_event(context)
    return gateway, repository, event


def _protected_event(
    context: FactorContext, *, preserve: tuple[str, ...] = ("S9.US",)
) -> TradeSignalEvent:
    return TradeSignalEvent(
        strategy_name="patchtst_e3",
        target_weights=tuple((f"S{i}.US", 0.25) for i in range(3)),
        rebalance_key="2025-01-02",
        reason="factor",
        preserve_positions=preserve,
        factor_context=context,
        not_before_ns=int(datetime(2025, 1, 3, 14, 30, tzinfo=UTC).timestamp() * 1e9),
        expires_at_ns=int(datetime(2025, 1, 3, 21, tzinfo=UTC).timestamp() * 1e9),
        ts_event=int(datetime(2025, 1, 3, tzinfo=UTC).timestamp() * 1e9),
        ts_init=int(datetime(2025, 1, 3, tzinfo=UTC).timestamp() * 1e9),
    )


def test_factor_keeps_missing_score_quantity_and_deducts_budget(tmp_path: Path) -> None:
    gateway, _, event = _factor_setup(tmp_path)
    plan, rejection = gateway._build_plan(event)
    assert rejection is None
    assert all(order.canonical_id != "S9.US" for order in plan)
    buys = [order for order in plan if order.side == OrderSide.BUY]
    assert len(buys) == 3
    assert sum(order.target_weight for order in buys) == pytest.approx(0.5)
    assert sum(order.quantity * order.price for order in buys) <= 5000
    assert [
        (order.canonical_id, order.quantity) for order in plan if order.side == OrderSide.SELL
    ] == [("S8.US", 10)]
    gateway.quantities["S9.US"] = 0
    plan, rejection = gateway._build_plan(event)
    assert rejection is None
    assert all(order.canonical_id != "S9.US" for order in plan)
    assert sum(order.target_weight for order in plan if order.side == OrderSide.BUY) == 0.75


@pytest.mark.parametrize(
    ("quantity", "age", "expected"),
    [
        (26, date(2025, 1, 2), "instrument risk"),
        (25, date(2024, 12, 30), "stale"),
        (25, None, "missing price"),
    ],
)
def test_factor_unvalued_or_overlimit_protection_skips_whole_plan(
    tmp_path: Path,
    quantity: int,
    age: date | None,
    expected: str,
) -> None:
    gateway, _, event = _factor_setup(tmp_path)
    gateway.quantities["S9.US"] = quantity
    key = "S9.US-1-DAY-LAST-EXTERNAL"
    if age is None:
        gateway.test_cache._bars.pop(key)
    else:
        gateway.test_cache._bars[key] = make_bar(age, instrument_id="S9.US", close=100)
    plan, rejection = gateway._build_plan(event)
    assert plan == ()
    assert rejection is not None
    assert expected in rejection


def test_factor_allows_one_session_old_protected_price_only(tmp_path: Path) -> None:
    gateway, _, event = _factor_setup(tmp_path)
    gateway.test_cache._bars["S9.US-1-DAY-LAST-EXTERNAL"] = make_bar(
        date(2024, 12, 31),
        instrument_id="S9.US",
        close=100,
    )
    assert gateway._build_plan(event)[1] is None
    gateway.test_cache._bars["S0.US-1-DAY-LAST-EXTERNAL"] = make_bar(
        date(2024, 12, 31),
        instrument_id="S0.US",
        close=100,
    )
    assert "stale" in str(gateway._build_plan(event)[1])


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("calendar_version", "temporary", "calendar mismatch"),
        ("model_release_id", "c" * 64, "release mismatch"),
        ("asof_date", "2024-12-31", "expected date"),
        ("source_kind", "evaluation_predictions", "paper factor acceptance"),
        ("eligible_count", 8, "protection context"),
        ("delivery_id", "e" * 64, "paper factor acceptance"),
    ],
)
def test_factor_execution_rechecks_source_context(
    tmp_path: Path,
    field: str,
    value: object,
    reason: str,
) -> None:
    gateway, _, event = _factor_setup(tmp_path)
    assert event.factor_context is not None
    changed = replace(event.factor_context, **{field: value})
    assert reason in str(gateway._build_plan(_protected_event(changed))[1])


def test_factor_approval_recalculates_protected_budget_and_stops_on_skip(tmp_path: Path) -> None:
    gateway, repository, event = _factor_setup(tmp_path)
    repository.register_signal_workflow(event, scope="paper:factor")
    gateway._process_signal(event)
    saved = repository.get_signal_workflow(str(event.id))
    assert saved is not None
    assert saved.status == "PENDING"
    assert "normal_budget=50.00%" in str(saved.risk_summary)
    assert saved.to_event().factor_context == event.factor_context
    assert repository.decide_manual_approval(
        str(event.id), approved=True, decision_by="test", timestamp_ns=event.ts_event + 1
    )
    assert repository.claim_next_approved(scope="other", timestamp_ns=event.not_before_ns) is None
    assert (
        repository.claim_next_approved(
            scope="paper:factor", timestamp_ns=event.not_before_ns - 1_000
        )
        is None
    )
    gateway.quantities["S9.US"] = 10
    plan, rejection = gateway._build_plan(saved.to_event())
    assert rejection is None
    assert sum(item.target_weight for item in plan if item.side == OrderSide.BUY) == pytest.approx(
        0.65
    )
    repository.record_factor_decision(
        scope="paper:factor",
        strategy_name="patchtst_e3",
        asof_date="2025-01-02",
        status="SKIP",
        reason="unscorable_fraction_exceeded",
        timestamp_ns=gateway.clock.timestamp_ns(),
        preserve_positions=event.preserve_positions,
        context=event.factor_context,
    )
    gateway.test_clock.set_time(event.not_before_ns)
    gateway._poll_approved_signal(None)
    saved = repository.get_signal_workflow(str(event.id))
    assert saved is not None
    assert saved.status == "RISK_REJECTED"
    assert "SKIP" in str(saved.risk_summary)


def test_factor_sell_callback_rebuilds_buys_from_actual_holdings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from trading_assistant.execution.gateway import _OrderContext

    gateway, _, event = _factor_setup(tmp_path)
    gateway.test_clock.set_time(event.not_before_ns)
    gateway.quantities.update({"S8.US": 0, "S9.US": 10})
    gateway._open_sells[str(event.id)] = {"sell-1"}
    gateway._pending_buys[str(event.id)] = (event, ())
    submitted: list[tuple[_PlannedOrder, ...]] = []
    monkeypatch.setattr(
        _PlanningGateway, "_submit_orders", lambda self, event, plan: submitted.append(plan)
    )
    context = _OrderContext(
        str(event.id), "patchtst_e3", "SELL", 10, True, "factor", event.ts_event
    )
    gateway._finish_sell(context, "sell-1", failed=False)
    assert len(submitted) == 1
    assert sum(order.target_weight for order in submitted[0]) == pytest.approx(0.65)
    assert all(order.side == OrderSide.BUY for order in submitted[0])


@pytest.mark.parametrize("changed_state", ["SKIP", "expired", "canceled"])
def test_factor_sell_callback_cannot_continue_stale_buys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    changed_state: str,
) -> None:
    from trading_assistant.execution.gateway import _OrderContext

    gateway, repository, event = _factor_setup(tmp_path)
    gateway.test_clock.set_time(
        event.expires_at_ns if changed_state == "expired" else event.not_before_ns
    )
    gateway._open_sells[str(event.id)] = {"sell-1"}
    gateway._pending_buys[str(event.id)] = (event, ())
    if changed_state == "SKIP":
        repository.record_factor_decision(
            scope="paper:factor",
            strategy_name="patchtst_e3",
            asof_date="2025-01-02",
            status="SKIP",
            reason="missing_batch",
            timestamp_ns=event.not_before_ns,
        )
    submitted: list[object] = []
    monkeypatch.setattr(
        _PlanningGateway, "_submit_orders", lambda self, event, plan: submitted.append(plan)
    )
    context = _OrderContext(
        str(event.id), "patchtst_e3", "SELL", 10, True, "factor", event.ts_event
    )
    gateway._finish_sell(context, "sell-1", failed=changed_state == "canceled")
    assert not submitted
    assert str(event.id) not in gateway._pending_buys


def test_restart_reconciles_only_proven_factor_orders_before_continuation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from nautilus_trader.model.objects import Quantity

    gateway, repository, event = _factor_setup(tmp_path)
    repository.register_signal_workflow(event, scope="paper:factor")
    gateway.test_clock.set_time(event.not_before_ns)
    repository.record_order_event(
        signal_event_id=str(event.id),
        order_event_id=None,
        timestamp_ns=event.ts_event,
        strategy_name=event.strategy_name,
        instrument_id="S8.US",
        client_order_id="owned",
        status="SUBMITTED",
        direction="SELL",
        quantity=10,
        reason="submitted before restart",
    )
    owned = SimpleNamespace(
        tags=None,
        client_order_id="owned",
        side=OrderSide.SELL,
        quantity=Quantity.from_int(10),
        is_pending_cancel=False,
    )
    unknown = SimpleNamespace(tags=None, client_order_id="manual")
    monkeypatch.setattr(
        _PlanningCache, "orders_open", lambda self, strategy_id=None: [owned, unknown]
    )
    canceled: list[object] = []
    monkeypatch.setattr(
        _PlanningGateway, "cancel_order", lambda self, order: canceled.append(order)
    )
    gateway._pending_buys[str(event.id)] = (event, ())
    gateway._reconcile_factor_orders()
    assert canceled == [owned]
    assert str(event.id) not in gateway._pending_buys
    states = repository.list_factor_decisions(scope="paper:factor")
    assert states[0].reason == "execution:restart reconciliation required"
    owned.is_pending_cancel = True
    gateway._reconcile_factor_orders()
    assert canceled == [owned]
    assert gateway._build_plan(event)[0] == ()


def test_cancel_rejection_keeps_factor_blocked_and_clears_pending_buys(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from trading_assistant.execution.gateway import _OrderContext

    gateway, repository, event = _factor_setup(tmp_path)
    repository.register_signal_workflow(event, scope="paper:factor")
    gateway._order_contexts["order"] = _OrderContext(
        str(event.id), "patchtst_e3", "SELL", 10, True, "factor", event.ts_event
    )
    gateway._pending_buys[str(event.id)] = (event, ())
    cancellation = SimpleNamespace(
        client_order_id="order",
        instrument_id="S8.US",
        id=UUID4(),
        ts_event=event.not_before_ns,
        reason="venue unavailable",
    )
    gateway.on_order_cancel_rejected(cancellation)
    assert str(event.id) not in gateway._pending_buys
    assert str(event.id) in gateway._failed_signals
    assert (
        "cancellation rejected" in repository.list_factor_decisions(scope="paper:factor")[0].reason
    )


def test_synchronous_sell_fills_wait_for_all_sells_and_count_only_submitted_buys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from nautilus_trader.common.factories import OrderFactory
    from nautilus_trader.model.identifiers import StrategyId, TraderId
    from nautilus_trader.model.orders import Order
    from nautilus_trader.test_kit.providers import TestInstrumentProvider

    gateway, repository, event = _factor_setup(tmp_path)
    repository.register_signal_workflow(event, scope="paper:factor")
    gateway.test_clock.set_time(event.not_before_ns)
    gateway.quantities["S7.US"] = 10
    factory = OrderFactory(TraderId("TESTER-001"), StrategyId("FACTOR-001"), gateway.clock)
    monkeypatch.setattr(_PlanningGateway, "order_factory", property(lambda self: factory))
    monkeypatch.setattr(
        _PlanningCache,
        "instrument",
        lambda self, instrument_id: TestInstrumentProvider.equity(
            str(instrument_id).split(".")[0], "US"
        ),
    )
    submitted: list[tuple[OrderSide, str]] = []

    from types import SimpleNamespace

    monkeypatch.setattr(
        _PlanningCache,
        "positions_open",
        lambda self, *, instrument_id, account_id: (
            [
                SimpleNamespace(
                    id=PositionId(f"{instrument_id}-EXTERNAL"),
                    signed_qty=gateway.quantities[str(instrument_id)],
                )
            ]
            if gateway.quantities.get(str(instrument_id), 0)
            else []
        ),
    )

    def submit_immediately(
        self: _PlanningGateway, order: Order, *, position_id: PositionId | None = None
    ) -> None:
        del position_id
        instrument = str(order.instrument_id)
        from types import SimpleNamespace

        self.on_order_submitted(
            SimpleNamespace(
                id=UUID4(),
                client_order_id=order.client_order_id,
                instrument_id=order.instrument_id,
                ts_event=self.clock.timestamp_ns(),
            )
        )
        submitted.append((order.side, instrument))
        quantity = int(order.quantity.as_double())
        self.quantities[instrument] = self.quantities.get(instrument, 0) + (
            quantity if order.side == OrderSide.BUY else -quantity
        )
        if order.side == OrderSide.SELL:
            self._finish_sell(
                self._order_contexts[str(order.client_order_id)],
                str(order.client_order_id),
                failed=False,
            )

    monkeypatch.setattr(_PlanningGateway, "submit_order", submit_immediately)
    plan, rejection = gateway._build_plan(event)
    assert rejection is None
    repository.claim_auto_signal(
        str(event.id),
        timestamp_ns=gateway.clock.timestamp_ns(),
        planned_orders=gateway._plan_payload(plan),
        risk_summary="test risk passed",
    )
    gateway._execute_plan(event, plan)
    assert submitted[:2] == [(OrderSide.SELL, "S7.US"), (OrderSide.SELL, "S8.US")]
    assert [side for side, _ in submitted[2:]] == [OrderSide.BUY] * 3
    assert sum(gateway._daily_new_positions.values()) == 3
    assert not gateway._pending_buys


def test_native_reports_restore_identity_replay_fills_and_preserve_terminal_state(
    tmp_path: Path,
) -> None:
    from nautilus_trader.cache.cache import Cache
    from nautilus_trader.execution.reports import FillReport, OrderStatusReport
    from nautilus_trader.model.enums import LiquiditySide, OrderStatus, OrderType, TimeInForce
    from nautilus_trader.model.identifiers import AccountId, ClientOrderId, TradeId, VenueOrderId
    from nautilus_trader.model.objects import Currency, Money, Price, Quantity

    gateway, repository, event = _factor_setup(tmp_path)
    invalidations: list[str] = []
    gateway._invalidate_broker = invalidations.append
    repository.register_signal_workflow(event, scope="paper:factor")
    repository.record_order_event(
        signal_event_id=str(event.id),
        order_event_id=None,
        timestamp_ns=event.ts_event,
        strategy_name=event.strategy_name,
        instrument_id="S0.US",
        client_order_id="persisted",
        status="SUBMITTED",
        direction="BUY",
        quantity=6,
        reason="before crash",
    )
    gateway.test_cache = Cache()

    def fill(trade_id: str, quantity: int, client: str = "persisted") -> FillReport:
        return FillReport(
            account_id=AccountId("IB-DU123"),
            instrument_id=InstrumentId.from_str("S0.US"),
            venue_order_id=VenueOrderId("17:901"),
            trade_id=TradeId(trade_id),
            order_side=OrderSide.BUY,
            last_qty=Quantity.from_int(quantity),
            last_px=Price.from_str("100.00"),
            commission=Money(0.1, Currency.from_str("USD")),
            liquidity_side=LiquiditySide.NO_LIQUIDITY_SIDE,
            report_id=UUID4(),
            ts_event=event.not_before_ns,
            ts_init=event.not_before_ns,
            client_order_id=ClientOrderId(client),
        )

    gateway._on_execution_report(fill("manual-fill", 1, "manual"))
    assert not repository.list_fill_audits(scope="paper:factor")
    first = fill("first", 2)
    gateway._on_execution_report(first)
    assert repository.list_latest_order_audits(scope="paper:factor")[0].status == "PARTIALLY_FILLED"
    gateway._order_contexts.clear()
    gateway._on_execution_report(first)
    gateway._on_execution_report(fill("second", 4))
    # 重连可能只有完整原始成交而没有对应订单报告, 仍应确认可证明的全部成交。
    assert repository.list_latest_order_audits(scope="paper:factor")[0].status == "FILLED"
    report = OrderStatusReport(
        account_id=AccountId("IB-DU123"),
        instrument_id=InstrumentId.from_str("S0.US"),
        venue_order_id=VenueOrderId("17:901"),
        client_order_id=ClientOrderId("persisted"),
        order_side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.DAY,
        order_status=OrderStatus.FILLED,
        quantity=Quantity.from_int(6),
        filled_qty=Quantity.from_int(6),
        report_id=UUID4(),
        ts_accepted=event.not_before_ns,
        ts_last=event.not_before_ns + 1_000_000_000,
        ts_init=event.not_before_ns + 1_000_000_000,
    )
    gateway._on_execution_report(report)
    gateway._on_execution_report(first)
    assert len(repository.list_fill_audits(scope="paper:factor")) == 2
    latest = repository.list_latest_order_audits(scope="paper:factor")[0]
    assert latest.status == "FILLED"
    assert latest.venue_order_id == "17:901"
    gateway._on_execution_report(fill("overfill", 1))
    assert gateway._recovery_error == "owned fill quantity exceeds audited order"
    assert len(repository.list_fill_audits(scope="paper:factor")) == 2
    gateway._on_execution_report(fill("first", 3))
    assert gateway._recovery_error == "fill audit requires reconciliation: ValueError"
    assert len(repository.list_fill_audits(scope="paper:factor")) == 2
    report.account_id = AccountId("IB-DU999")
    gateway._on_execution_report(report)
    assert gateway._recovery_error is not None
    assert invalidations == [
        "owned fill quantity exceeds audited order",
        "fill audit requires reconciliation: ValueError",
        gateway._recovery_error,
    ]


@pytest.mark.parametrize("quantities", [(5, 5), (-10,), (3,), ()])
def test_invalid_second_position_rejects_entire_sell_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, quantities: tuple[int, ...]
) -> None:
    from types import SimpleNamespace

    gateway, repository, event = _factor_setup(tmp_path)
    repository.register_signal_workflow(event, scope="paper:factor")
    monkeypatch.setattr(
        _PlanningCache,
        "positions_open",
        lambda self, *, instrument_id, account_id: [
            SimpleNamespace(id=PositionId(f"{instrument_id}-{index}"), signed_qty=quantity)
            for index, quantity in enumerate((10,) if str(instrument_id) == "S7.US" else quantities)
        ],
    )
    submitted: list[object] = []
    monkeypatch.setattr(_PlanningGateway, "submit_order", lambda *args, **_: submitted.append(args))
    plan = tuple(
        _PlannedOrder(canonical, canonical, OrderSide.SELL, 10, 100, 0, False)
        for canonical in ("S7.US", "S8.US")
    )
    gateway._execute_plan(event, plan)
    assert submitted == []
    assert repository.list_order_audits(scope="paper:factor") == ()
    assert str(event.id) in gateway._failed_signals
    assert str(event.id) not in gateway._pending_buys


def test_recovery_requires_owned_positions_complete_fills_and_terminal_orders(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    gateway, repository, event = _factor_setup(tmp_path)
    repository.register_signal_workflow(event, scope="paper:factor")
    current = dict.fromkeys(gateway._settings.instrument_routes, 0)
    monkeypatch.setattr(_PlanningCache, "order", lambda self, _: None, raising=False)
    assert gateway._ownership_rejection(current) is None
    order_args = {
        "signal_event_id": str(event.id),
        "order_event_id": None,
        "timestamp_ns": event.ts_event,
        "strategy_name": event.strategy_name,
        "instrument_id": "S0.US",
        "client_order_id": "order",
        "direction": "BUY",
        "quantity": 6,
        "reason": "test",
    }
    repository.record_order_event(**order_args, status="CREATED")
    assert "unresolved" in str(gateway._ownership_rejection(current))
    repository.record_order_event(**order_args, status="CANCELED")
    assert gateway._ownership_rejection(current) is None
    monkeypatch.setattr(
        _PlanningCache,
        "order",
        lambda self, _: SimpleNamespace(
            is_closed=False, filled_qty=SimpleNamespace(as_double=lambda: 0)
        ),
        raising=False,
    )
    assert "still open" in str(gateway._ownership_rejection(current))
    repository.record_order_event(**order_args, status="FILLED")
    assert "fills missing" in str(gateway._ownership_rejection(current))
    repository.record_fill(
        run_id=None,
        signal_event_id=str(event.id),
        trade_id="fill",
        timestamp_ns=event.ts_event,
        strategy_name=event.strategy_name,
        instrument_id="S0.US",
        client_order_id="order",
        direction="BUY",
        quantity=6,
        price=100,
        commission=0,
    )
    assert "ownership" in str(gateway._ownership_rejection(current))
    current["S0.US"] = 6
    monkeypatch.setattr(
        _PlanningCache,
        "positions_open",
        lambda self, instrument_id, account_id: (
            [_Position(6)] if str(instrument_id) == "S0.US" else []
        ),
    )
    monkeypatch.setattr(
        _PlanningCache,
        "order",
        lambda self, _: SimpleNamespace(
            is_closed=True, filled_qty=SimpleNamespace(as_double=lambda: 6)
        ),
        raising=False,
    )
    assert gateway._ownership_rejection(current) is None
    # 拆股或手工调整造成数量变化时, 不能自动把差额纳入模型仓位。
    monkeypatch.setattr(
        _PlanningCache,
        "positions_open",
        lambda self, instrument_id, account_id: (
            [_Position(12)] if str(instrument_id) == "S0.US" else []
        ),
    )
    assert "corporate action" in str(gateway._ownership_rejection(current))


def test_fixed_release_does_not_adopt_another_strategys_signal_or_order(tmp_path: Path) -> None:
    """共用历史审计库时, 826 不接管旧策略工作流或券商回报。"""
    gateway, repository, _ = _factor_setup(tmp_path)
    legacy = _planning_event(timestamp_ns=gateway.clock.timestamp_ns())
    repository.register_signal_workflow(legacy, scope="paper:factor")
    assert gateway._factor_rejection(legacy, execution=False) == "missing factor context"
    repository.record_order_event(
        signal_event_id=str(legacy.id),
        order_event_id=None,
        timestamp_ns=legacy.ts_event,
        strategy_name=legacy.strategy_name,
        instrument_id="S0.US",
        client_order_id="other-strategy-order",
        status="SUBMITTED",
        direction="BUY",
        quantity=1,
        reason="other strategy",
    )
    assert gateway._restore_order_context("other-strategy-order") is None
    assert not gateway._order_contexts


def test_auto_poll_never_claims_old_manual_approval(tmp_path: Path) -> None:
    gateway, repository = _gateway(tmp_path, approval_mode="auto")
    event = _event(repository)
    repository.prepare_manual_approval(
        str(event.id), planned_orders=(), risk_summary="manual", timestamp_ns=event.ts_event
    )
    repository.decide_manual_approval(
        str(event.id), approved=True, decision_by="operator", timestamp_ns=event.ts_event
    )
    gateway._poll_approved_signal(cast(TimeEvent, object()))
    assert not gateway.executed
    workflow = repository.get_signal_workflow(str(event.id))
    assert workflow is not None
    assert workflow.status == "APPROVED"


def test_known_split_between_reference_and_execution_requires_review(tmp_path: Path) -> None:
    from decimal import Decimal

    from msgspec.structs import replace as replace_config

    from trading_assistant.data.corporate_actions import (
        CorporateActionRepository,
        CorporateActions,
        SplitAction,
    )

    gateway, _, event = _factor_setup(tmp_path)
    path = tmp_path / "catalog-actions"
    CorporateActionRepository(path).write(
        CorporateActions("S0.US", (), (SplitAction(date(2025, 1, 3), Decimal(2)),))
    )
    gateway._settings = replace_config(gateway._settings, corporate_action_path=str(path))
    assert "cross-session split" in str(gateway._build_plan(event)[1])


def test_auto_deferred_account_can_recover_but_expired_signal_cannot_trade(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway, repository = _gateway(tmp_path, approval_mode="auto")
    event = _event(repository)
    monkeypatch.setattr(type(gateway), "_account_rejection", lambda self: "account stale")
    gateway._handle_signal(event)
    assert not gateway.executed
    assert str(event.id) in gateway._deferred_signals
    monkeypatch.setattr(type(gateway), "_account_rejection", lambda self: None)
    gateway._poll_approved_signal(cast(TimeEvent, object()))
    assert len(gateway.executed) == 1
    assert repository.count(ApprovalRecord) == 1
    expired = _event(repository)
    monkeypatch.setattr(type(gateway), "_account_rejection", lambda self: "account stale")
    gateway.test_clock.set_time(expired.expires_at_ns)
    gateway._poll_approved_signal(cast(TimeEvent, object()))
    assert len(gateway.executed) == 1
    workflow = repository.get_signal_workflow(str(expired.id))
    assert workflow is not None
    assert workflow.status == "EXPIRED"


@pytest.mark.parametrize("terminal", ["canceled", "expired", "filled"])
def test_native_partial_fill_and_late_replay_cannot_advance_terminal_sell_twice(
    tmp_path: Path,
    terminal: str,
) -> None:
    from nautilus_trader.cache.cache import Cache
    from nautilus_trader.common.factories import OrderFactory
    from nautilus_trader.model.enums import TimeInForce
    from nautilus_trader.model.identifiers import AccountId, StrategyId, TradeId, TraderId
    from nautilus_trader.model.objects import Quantity
    from nautilus_trader.test_kit.providers import TestInstrumentProvider
    from nautilus_trader.test_kit.stubs.events import TestEventStubs

    gateway, repository, event = _factor_setup(tmp_path)
    repository.register_signal_workflow(event, scope="paper:factor")
    cache = Cache()
    gateway.test_cache = cache
    instrument = TestInstrumentProvider.equity("S8", "US")
    cache.add_instrument(instrument)
    factory = OrderFactory(TraderId("TESTER-001"), StrategyId("EXTERNAL"), gateway.clock)
    order = factory.market(
        instrument_id=instrument.id,
        order_side=OrderSide.SELL,
        quantity=Quantity.from_int(6),
        time_in_force=TimeInForce.DAY,
    )
    cache.add_order(order)
    client_id = str(order.client_order_id)
    repository.record_order_event(
        signal_event_id=str(event.id),
        order_event_id=None,
        timestamp_ns=event.ts_event,
        strategy_name=event.strategy_name,
        instrument_id=str(instrument.id),
        client_order_id=client_id,
        status="CREATED",
        direction="SELL",
        quantity=6,
        reason="persisted",
    )
    gateway._open_sells[str(event.id)] = {client_id}
    if terminal != "filled":
        gateway._pending_buys[str(event.id)] = (event, ())
    submitted = TestEventStubs.order_submitted(
        order, account_id=AccountId("IB-DU123"), ts_event=event.not_before_ns
    )
    order.apply(submitted)
    gateway._restore_order_context(client_id)
    gateway.on_order_submitted(submitted)
    accepted = TestEventStubs.order_accepted(
        order, account_id=AccountId("IB-DU123"), ts_event=event.not_before_ns
    )
    order.apply(accepted)
    gateway._on_external_order_event(accepted)
    partial_fill = TestEventStubs.order_filled(
        order,
        instrument,
        account_id=AccountId("IB-DU123"),
        last_qty=Quantity.from_int(2),
        trade_id=TradeId("partial"),
        ts_event=event.not_before_ns + 1_000_000_000,
    )
    order.apply(partial_fill)
    gateway._on_external_order_event(partial_fill)
    assert repository.list_latest_order_audits(scope="paper:factor")[0].status == "PARTIALLY_FILLED"
    assert gateway._open_sells[str(event.id)] == {client_id}
    if terminal == "filled":
        last = TestEventStubs.order_filled(
            order,
            instrument,
            account_id=AccountId("IB-DU123"),
            last_qty=Quantity.from_int(4),
            trade_id=TradeId("final"),
            ts_event=event.not_before_ns + 2_000_000_000,
        )
    else:
        last = getattr(TestEventStubs, f"order_{terminal}")(
            order, ts_event=event.not_before_ns + 2_000_000_000
        )
    order.apply(last)
    gateway._on_external_order_event(last)
    gateway._on_external_order_event(partial_fill)
    assert repository.list_latest_order_audits(scope="paper:factor")[0].status == terminal.upper()
    assert len(repository.list_fill_audits(scope="paper:factor")) == (
        2 if terminal == "filled" else 1
    )
    assert not gateway._pending_buys

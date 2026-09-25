"""只读组合快照 Actor 测试。"""

import ast
from pathlib import Path
from types import SimpleNamespace
from typing import cast

from nautilus_trader.common.component import TestClock, TimeEvent
from nautilus_trader.model.enums import PositionSide
from nautilus_trader.model.objects import Currency
from nautilus_trader.model.position import Position

from trading_assistant.live.portfolio_snapshot import (
    PortfolioSnapshotActor,
    PortfolioSnapshotActorConfig,
    account_not_ready_reason,
    account_update,
    position_snapshot_input,
)
from trading_assistant.storage.repository import TradingRepository


def test_account_freshness_uses_native_account_event_not_snapshot_clock() -> None:
    from nautilus_trader.accounting.accounts.margin import MarginAccount
    from nautilus_trader.test_kit.stubs.events import TestEventStubs

    account = MarginAccount(TestEventStubs.margin_account_state(), calculate_account_state=False)
    assert (
        account_not_ready_reason(
            account,
            now_ns=299_000_000_000,
            stale_after_seconds=300,
            broker_connected=True,
            reconciliation_complete=True,
        )
        is None
    )
    assert "stale" in str(
        account_not_ready_reason(
            account,
            now_ns=301_000_000_000,
            stale_after_seconds=300,
            broker_connected=True,
            reconciliation_complete=True,
        )
    )
    assert "disconnected" in str(
        account_not_ready_reason(
            account,
            now_ns=1,
            stale_after_seconds=300,
            broker_connected=False,
            reconciliation_complete=True,
        )
    )
    assert "reconciliation" in str(
        account_not_ready_reason(
            account,
            now_ns=1,
            stale_after_seconds=300,
            broker_connected=True,
            reconciliation_complete=False,
        )
    )


def test_other_currency_update_does_not_refresh_usd_clock_or_cash() -> None:
    """最新 EUR 回报不能冒充 USD 资金更新, 使用真实 NT 账户累积回报。"""
    from nautilus_trader.accounting.accounts.margin import MarginAccount
    from nautilus_trader.core.uuid import UUID4
    from nautilus_trader.model.enums import AccountType
    from nautilus_trader.model.events import AccountState
    from nautilus_trader.model.objects import AccountBalance, Money
    from nautilus_trader.test_kit.stubs.events import TestEventStubs

    usd = Currency.from_str("USD")
    eur = Currency.from_str("EUR")
    source = TestEventStubs.margin_account_state()
    initial = AccountState(
        account_id=source.account_id,
        account_type=AccountType.MARGIN,
        base_currency=None,
        reported=True,
        balances=source.balances,
        margins=[],
        info={"TotalCashValue": 10_000.0},
        event_id=UUID4(),
        ts_event=0,
        ts_init=0,
    )
    account = MarginAccount(initial, calculate_account_state=False)
    other = AccountState(
        account_id=account.id,
        account_type=AccountType.MARGIN,
        base_currency=None,
        reported=True,
        balances=[AccountBalance(Money(100, eur), Money(0, eur), Money(100, eur))],
        margins=[],
        info={"TotalCashValue": 100.0},
        event_id=UUID4(),
        ts_event=299_000_000_000,
        ts_init=299_000_000_000,
    )
    account.apply(other)
    assert account_update(account, usd) == initial
    assert account_update(account, eur) == other
    assert "stale" in str(
        account_not_ready_reason(
            account,
            now_ns=301_000_000_000,
            stale_after_seconds=300,
            broker_connected=True,
            reconciliation_complete=True,
        )
    )


class _Money:
    def as_double(self) -> float:
        return 3.5


class _Position:
    instrument_id = "SPY.ARCA"
    signed_qty = 4
    side = PositionSide.LONG
    avg_px_open = 600.25
    realized_pnl = _Money()


class _Account:
    last_event = SimpleNamespace(
        ts_event=0,
        info={"TotalCashValue": 2.5},
        balances=[SimpleNamespace(currency=Currency.from_str("USD"))],
    )
    events = (last_event,)

    def balance_total(self, _: object) -> _Money:
        return _Money()

    def balance_free(self, _: object) -> _Money:
        return _Money()

    def balance_locked(self, _: object) -> _Money:
        return _Money()


class _Portfolio:
    def __init__(self) -> None:
        self.account_value: _Account | None = _Account()

    def account(self, *, account_id: object) -> _Account | None:
        del account_id
        return self.account_value


class _Cache:
    def positions_open(self, *, account_id: object) -> list[Position]:
        del account_id
        return [cast(Position, _Position())]


class _Log:
    def __init__(self) -> None:
        self.warnings: list[str] = []

    def info(self, _: str) -> None:
        pass

    def warning(self, message: str) -> None:
        self.warnings.append(message)


class _ActorHarness(PortfolioSnapshotActor):
    def __init__(self, config: PortfolioSnapshotActorConfig) -> None:
        super().__init__(config)
        self.test_clock = TestClock()
        self.test_portfolio = _Portfolio()
        self.test_cache = _Cache()
        self.test_log = _Log()

    @property
    def clock(self) -> TestClock:
        return self.test_clock

    @property
    def portfolio(self) -> _Portfolio:
        return self.test_portfolio

    @property
    def cache(self) -> _Cache:
        return self.test_cache

    @property
    def log(self) -> _Log:
        return self.test_log


def test_converts_nt_position_at_storage_boundary() -> None:
    snapshot = position_snapshot_input(cast(Position, _Position()))
    assert snapshot.instrument_id == "SPY.ARCA"
    assert snapshot.signed_quantity == 4.0
    assert snapshot.side == "LONG"
    assert snapshot.avg_open_price == 600.25
    assert snapshot.realized_pnl == 3.5


def test_snapshot_actor_config_and_module_are_read_only() -> None:
    config = PortfolioSnapshotActorConfig(
        account_id="IB-DU123",
        database_url="sqlite:///audit.db",
        snapshot_interval_seconds=30,
    )
    assert config.currency == "USD"
    source_path = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "trading_assistant"
        / "live"
        / "portfolio_snapshot.py"
    )
    calls = {
        node.func.attr
        for node in ast.walk(ast.parse(source_path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "submit_order" not in calls
    assert "cancel_order" not in calls


def test_actor_records_from_nt_interfaces_and_stops_cleanly(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path}/snapshot.db"
    actor = _ActorHarness(
        PortfolioSnapshotActorConfig(
            account_id="IB-DU123",
            database_url=database_url,
            snapshot_interval_seconds=30,
        )
    )
    actor.on_start()
    actor._capture_snapshot(cast(TimeEvent, object()))

    repository = TradingRepository(database_url)
    snapshot = repository.latest_portfolio_snapshot(account_id="IB-DU123")
    assert snapshot is not None
    assert snapshot.net_liquidation == 3.5
    assert snapshot.available_funds == 3.5
    assert snapshot.total_cash_value == 2.5
    assert snapshot.broker_connected is None
    assert snapshot.positions[0].signed_quantity == 4.0
    repository.close()

    actor.test_portfolio.account_value = None
    actor._capture_snapshot(cast(TimeEvent, object()))
    assert actor.test_log.warnings
    actor.on_stop()
    assert actor._repository is None


def test_snapshot_waits_for_its_currency_without_fabricating_zero_balance(tmp_path: Path) -> None:
    actor = _ActorHarness(
        PortfolioSnapshotActorConfig(
            account_id="IB-DU123", database_url=f"sqlite:///{tmp_path}/snapshot.db"
        )
    )
    assert actor.test_portfolio.account_value is not None
    actor.test_portfolio.account_value.events = ()
    actor.on_start()
    actor._capture_snapshot(cast(TimeEvent, object()))
    assert actor._repository is not None
    assert actor._repository.latest_portfolio_snapshot(account_id="IB-DU123") is None
    assert "USD" in actor.test_log.warnings[-1]
    actor.on_stop()


def test_stop_immediately_invalidates_snapshot_without_refreshing_broker_time(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite:///{tmp_path}/stopped.db"
    actor = _ActorHarness(
        PortfolioSnapshotActorConfig(account_id="IB-DU123", database_url=database_url)
    )
    actor.bind_broker_status(lambda: (True, True))
    actor.on_start()
    actor._capture_snapshot(cast(TimeEvent, object()))
    actor.test_clock.set_time(1_000_000_000)
    actor.on_stop()
    repository = TradingRepository(database_url)
    try:
        latest = repository.latest_portfolio_snapshot(account_id="IB-DU123")
        assert latest is not None
        assert latest.reconciliation_complete is False
        assert latest.not_ready_reason == "trading node stopped"
        assert latest.timestamp_utc.timestamp() == 1
        assert latest.account_updated_at_utc is not None
        assert latest.account_updated_at_utc.timestamp() == 0
        assert latest.positions[0].signed_quantity == 4
    finally:
        repository.close()

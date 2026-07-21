"""只读组合快照 Actor 测试。"""

import ast
from pathlib import Path
from typing import cast

from nautilus_trader.common.component import TestClock, TimeEvent
from nautilus_trader.model.position import Position

from trading_assistant.live.portfolio_snapshot import (
    PortfolioSnapshotActor,
    PortfolioSnapshotActorConfig,
    position_snapshot_input,
)
from trading_assistant.storage.repository import TradingRepository


class _Money:
    def as_double(self) -> float:
        return 3.5


class _Position:
    instrument_id = "SPY.ARCA"
    signed_qty = 4
    side = "LONG"
    avg_px_open = 600.25
    realized_pnl = _Money()


class _Account:
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
    assert snapshot.positions[0].signed_quantity == 4.0
    repository.close()

    actor.test_portfolio.account_value = None
    actor._capture_snapshot(cast(TimeEvent, object()))
    assert actor.test_log.warnings
    actor.on_stop()
    assert actor._repository is None

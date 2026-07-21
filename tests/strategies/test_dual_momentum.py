"""双动量 Actor 配置与 Catalog 启动测试。"""

from datetime import UTC, date, datetime
from pathlib import Path

from nautilus_trader.common.component import TestClock
from nautilus_trader.core.uuid import UUID4

from tests.data.helpers import make_bar
from trading_assistant.strategies.dual_momentum import (
    DualMomentumActor,
    DualMomentumActorConfig,
)


class _ActorHarness(DualMomentumActor):
    def __init__(self, config: DualMomentumActorConfig) -> None:
        super().__init__(config)
        self.test_clock = TestClock()
        self.published: list[tuple[int, str]] = []

    @property
    def clock(self) -> TestClock:
        return self.test_clock

    def _publish_signal(self, timestamp_ns: int, as_of_month: str) -> None:
        self.published.append((timestamp_ns, as_of_month))


def _actor(tmp_path: Path) -> _ActorHarness:
    return _ActorHarness(
        DualMomentumActorConfig(
            bar_types=("SPY.ARCA-1-DAY-LAST-EXTERNAL",),
            instrument_ids=("SPY.ARCA",),
            lookback_months=6,
            top_n=1,
            fallback_instrument="BIL.ARCA",
            signal_expiry_hours=4,
            database_url=f"sqlite:///{tmp_path}/actor.db",
            stream_bars=False,
            bootstrap_from_catalog=True,
        )
    )


def test_actor_config_accepts_native_bar_type_strings() -> None:
    config = DualMomentumActorConfig(
        bar_types=("SPY.ARCA-1-DAY-LAST-EXTERNAL",),
        instrument_ids=("SPY.ARCA",),
        lookback_months=6,
        top_n=3,
        fallback_instrument="BIL.ARCA",
        signal_expiry_hours=4,
        database_url="sqlite:///:memory:",
    )
    assert config.bar_types == ("SPY.ARCA-1-DAY-LAST-EXTERNAL",)


def test_catalog_bootstrap_emits_latest_completed_month_once(tmp_path: Path) -> None:
    actor = _actor(tmp_path)
    now = datetime(2026, 7, 18, tzinfo=UTC)
    actor.test_clock.set_time(int(now.timestamp() * 1_000_000_000))
    actor._monthly_closes = {
        "2026-05": {"SPY.ARCA": 100.0},
        "2026-06": {"SPY.ARCA": 110.0},
        "2026-07": {"SPY.ARCA": 108.0},
    }
    actor._catalog_requests = {"SPY", "QQQ"}
    actor._catalog_request_completed("SPY", UUID4())
    assert actor.published == []
    actor._catalog_request_completed("QQQ", UUID4())
    assert actor.published == [(actor.test_clock.timestamp_ns(), "2026-06")]


def test_historical_bars_do_not_emit_month_transitions(tmp_path: Path) -> None:
    actor = _actor(tmp_path)
    actor.on_historical_data(make_bar(date(2026, 1, 30)))
    actor.on_historical_data(make_bar(date(2026, 2, 27)))
    assert actor.published == []
    actor._process_complete_session(
        int(datetime(2026, 3, 2, tzinfo=UTC).timestamp() * 1_000_000_000),
        {"SPY.ARCA": 120.0},
    )
    assert actor.published[-1][1] == "2026-02"

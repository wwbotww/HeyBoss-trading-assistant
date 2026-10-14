"""六个真实业务服务在独立 NT Catalog/SQLite 上的每日联合验收。"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import cast
from urllib.parse import parse_qs, urlparse

import pytest

from tests.data.helpers import make_bar
from tests.market_radar.test_daily import NOW, ROOT
from tests.market_radar.test_eodhd_economic_events import economic_payload
from tests.market_radar.test_service import (
    _calendar_response,
    _earnings_classification,
    _earnings_membership,
    _FakeClassificationSource,
    _FakeFredSource,
    _FakeMembershipSource,
    _fundamental_response,
)
from trading_assistant.application.market_radar import MarketRadarQueryService
from trading_assistant.data.catalog import CatalogRepository
from trading_assistant.data.config import InstrumentSpec
from trading_assistant.data.market_calendar import regular_sessions
from trading_assistant.data.pipeline import PipelineSummary
from trading_assistant.market_radar import daily, service
from trading_assistant.market_radar.config import MarketRadarDailyConfig
from trading_assistant.market_radar.economic_events import EconomicEventSnapshot
from trading_assistant.market_radar.fred import FredObservation, FredObservationBatch
from trading_assistant.market_radar.runtime import load_market_radar_runtime
from trading_assistant.market_radar.schedule import DAILY_JOBS, expected_price_date
from trading_assistant.market_radar.storage import MarketRadarRepository


@pytest.mark.parametrize("actual", [0.0, -0.12, None])
def test_daily_economic_lookback_recovers_actual_without_rewriting_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    actual: float | None,
) -> None:
    runtime = load_market_radar_runtime(ROOT, {"HEYBOSS_RUNTIME_ROOT": str(tmp_path)})
    # 只选择本次验收的来源; 仍走真实每日调度、服务、分页适配器和 SQLite 发布。
    monkeypatch.setattr(daily, "DAILY_JOBS", ("economic_events",))
    calls: list[dict[str, list[str]]] = []
    observed = NOW

    def clock() -> datetime:
        return observed

    def transport(url: str, _timeout: int) -> bytes:
        query = parse_qs(urlparse(url).query)
        calls.append(query)
        assert query["from"] == [(observed.date() - timedelta(days=7)).isoformat()]
        assert query["to"] == [(observed.date() + timedelta(days=30)).isoformat()]
        value = None if observed.date() == NOW.date() else actual
        return json.dumps([economic_payload(date=NOW.date().isoformat(), actual=value)]).encode()

    monkeypatch.setattr(service, "download", transport)
    settings = MarketRadarDailyConfig(time(6))
    for offset in (0, 0, 1, 1):
        observed = NOW + timedelta(days=offset)
        result = daily.run_market_radar_tick(
            runtime=runtime,
            settings=settings,
            environ={"EODHD_API_TOKEN": "test"},
            now=observed,
            clock=clock,
        )
        assert [(item.job, item.state) for item in result.jobs] == [("economic_events", "complete")]
        assert len(calls) == offset + 1
        reader = MarketRadarRepository(runtime.database_url, read_only=True)
        try:
            events = MarketRadarQueryService(repository=reader, clock=clock).events()
            window = events.days if offset == 0 else events.recent_economic.days
            items = [item for day in window for item in day.economic_events]
            assert len(items) == 1
            assert items[0].actual == (None if offset == 0 else actual)
            assert len(reader.daily_attempts(job="economic_events")) == offset + 1
            assert all(
                item.attempt_number == 1 for item in reader.daily_attempts(job="economic_events")
            )
        finally:
            reader.close()
    with sqlite3.connect(f"file:{runtime.database_path}?mode=ro", uri=True) as connection:
        rows = connection.execute(
            "SELECT payload_json FROM economic_event_snapshots ORDER BY as_of_date"
        ).fetchall()
    assert len(rows) == 2
    original = EconomicEventSnapshot.from_payload(json.loads(rows[0][0]))
    latest = EconomicEventSnapshot.from_payload(json.loads(rows[1][0]))
    assert original.batch.events[0].actual is None
    assert latest.batch.events[0].actual == actual
    assert original.as_of_date == NOW.date()
    assert latest.as_of_date == observed.date()


def test_six_sources_publish_real_snapshots_then_restart_skips_all_remote_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = load_market_radar_runtime(ROOT, {"HEYBOSS_RUNTIME_ROOT": str(tmp_path)})
    target = expected_price_date(NOW)
    calls: list[str] = []
    protected = tmp_path / "catalog" / "production-sentinel"
    protected.parent.mkdir()
    protected.write_bytes(b"trading catalog must not change")

    def price_response(
        instruments: tuple[InstrumentSpec, ...], start: date, end: date
    ) -> PipelineSummary:
        calls.append("prices")
        catalog = CatalogRepository(runtime.catalog_path)
        days = regular_sessions(start, end)
        bars = [
            make_bar(
                day,
                instrument_id=spec.instrument_id,
                bar_type_suffix="1-DAY-LAST-INTERNAL",
                close=100 + index / 100,
                high=110 + index / 100,
                low=90,
                open_price=100,
            )
            for spec in instruments
            for index, day in enumerate(days)
        ]
        written = catalog.append_new_bars(bars)
        return PipelineSummary(
            len(instruments), len(bars), written, 0, (), (), tmp_path / "quality.json"
        )

    async def sync_prices(**kwargs: object) -> PipelineSummary:
        assert kwargs["catalog_path"] == runtime.catalog_path
        return price_response(
            cast(tuple[InstrumentSpec, ...], kwargs["instruments"]),
            cast(date, kwargs["start_date"]),
            cast(date, kwargs["end_date"]),
        )

    def transport(url: str, timeout: int) -> bytes:
        path = urlparse(url).path
        calls.append(path)
        if "/fundamentals/" in path:
            return _fundamental_response(url, timeout)
        if "economic-events" in path:
            return b"[]"
        return _calendar_response(url, timeout)

    membership = replace(_earnings_membership(), membership_date=target)
    monkeypatch.setattr(
        daily, "StateStreetSpyHoldingsSource", lambda **_: _FakeMembershipSource(membership)
    )
    monkeypatch.setattr(
        daily,
        "EodhdIndexComponentsSource",
        lambda **_: _FakeClassificationSource(_earnings_classification()),
    )
    fred = _FakeFredSource(
        FredObservationBatch(
            series_id="DFII10",
            missing_values=0,
            observations=tuple(
                FredObservation(
                    series_id="DFII10",
                    observation_date=day,
                    value=1.5 + index / 1000,
                    realtime_start=NOW.date(),
                    realtime_end=NOW.date(),
                )
                for index, day in enumerate(regular_sessions(target - timedelta(days=1100), target))
            ),
        )
    )
    monkeypatch.setattr(service, "FredApiObservationSource", lambda **_: fred)
    monkeypatch.setattr(service, "sync_historical_specs", sync_prices)
    monkeypatch.setattr(service, "download", transport)
    settings = MarketRadarDailyConfig(time(6), poll_interval_seconds=1)
    result = daily.run_market_radar_tick(
        runtime=runtime,
        settings=settings,
        environ={"EODHD_API_TOKEN": "test", "FRED_API_KEY": "test"},
        now=NOW,
        clock=lambda: NOW,
    )
    assert [(item.job, item.state, item.reason) for item in result.jobs] == [
        (job, "complete", None) for job in DAILY_JOBS
    ]
    count = len(calls)
    # 新进程重新核对已持久化快照及实际 NT 文件, 不依赖上次进程缓存。
    second = daily.run_market_radar_tick(
        runtime=runtime, settings=settings, environ={}, now=NOW, clock=lambda: NOW
    )
    assert not second.has_errors
    assert len(calls) == count
    repository = MarketRadarRepository(runtime.database_url, read_only=True)
    try:
        status = MarketRadarQueryService(repository=repository, clock=lambda: NOW).sync_status()
        assert all(job.target_satisfied for job in status.jobs)
        assert len(repository.daily_attempts()) == 6
        assert all(item.status == "complete" for item in repository.daily_attempts())
        assert status.worker_state == "stopped"
    finally:
        repository.close()
    assert protected.read_bytes() == b"trading catalog must not change"

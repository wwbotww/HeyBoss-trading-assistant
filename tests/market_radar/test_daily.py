"""持久化重试、睡眠恢复、源间隔离与发布中断窗口。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from threading import Event

import pytest

from tests.market_radar.test_storage import _snapshot
from trading_assistant.application.market_radar import MarketRadarQueryService
from trading_assistant.data.eodhd_http import EodhdTemporaryHttpError
from trading_assistant.market_radar import daily, service
from trading_assistant.market_radar.config import MarketRadarDailyConfig
from trading_assistant.market_radar.economic_events import EconomicEventBatch, EconomicEventSnapshot
from trading_assistant.market_radar.metrics import PriceCoverage
from trading_assistant.market_radar.runtime import load_market_radar_runtime
from trading_assistant.market_radar.schedule import (
    DAILY_JOBS,
    DailyJob,
    expected_price_date,
    scheduled_at,
)
from trading_assistant.market_radar.service import MarketRadarSyncError
from trading_assistant.market_radar.storage import DailySyncAttempt, MarketRadarRepository

NOW = datetime(2026, 9, 30, 6, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[2]


def runner(tmp_path: Path, *, now: datetime = NOW, budget: int = 4) -> daily._DailyRunner:
    runtime = load_market_radar_runtime(ROOT, {"HEYBOSS_RUNTIME_ROOT": str(tmp_path)})
    return daily._DailyRunner(
        runtime,
        MarketRadarDailyConfig(time(6), max_attempts_per_day=budget),
        {},
        lambda: now,
        Event(),
    )


def publish_price(
    repository: MarketRadarRepository,
    run_id: str,
    *,
    now: datetime = NOW,
    partial: bool = False,
    already_started: bool = False,
) -> None:
    target = expected_price_date(now)
    if not already_started:
        repository.start_sync_run(
            run_id=run_id,
            source="eodhd_prices",
            started_at_utc=now,
            requested_start_date=target - timedelta(days=1),
            requested_end_date=target,
            instrument_count=2,
        )
    snapshot = _snapshot(as_of_date=target, calculated_at=now)
    if partial:
        snapshot = replace(snapshot, coverage=PriceCoverage(2, 1, 0.5))
    repository.publish_price_snapshot_and_complete(
        run_id,
        snapshot=snapshot,
        completed_at_utc=now,
        instruments_processed=2,
        bars_fetched=2,
        bars_written=2,
    )


@pytest.mark.parametrize("retryable", [True, False])
def test_attempt_budget_survives_restart_and_is_source_isolated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, retryable: bool
) -> None:
    calls: list[DailyJob] = []

    async def collect(self: daily._DailyRunner, job: DailyJob, target: date, run_id: str) -> None:
        calls.append(job)
        raise MarketRadarSyncError(
            "temporary_source" if retryable else "data_quality", retryable=retryable
        )

    monkeypatch.setattr(daily._DailyRunner, "_sync_job", collect)
    for index, hours in enumerate((0, 0.5, 1, 2)):
        current = runner(tmp_path, now=NOW + timedelta(hours=hours), budget=2)
        result = current.tick(current.clock())
        assert result.has_errors
        assert result.jobs[-1].state == "waiting_dependency"
        assert not current.repository.daily_attempts(job="breadth")
        expected_count = 2 if retryable and index >= 2 else 1
        assert len(current.repository.daily_attempts(job="economic_events")) == expected_count
        current.repository.close()
    assert set(calls) == set(DAILY_JOBS) - {"breadth"}
    assert len(calls) == (10 if retryable else 5)


def test_partial_price_publishes_but_missing_target_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    current = runner(tmp_path)

    async def collect(self: daily._DailyRunner, job: DailyJob, target: date, run_id: str) -> None:
        publish_price(self.repository, run_id, partial=True)

    monkeypatch.setattr(daily._DailyRunner, "_sync_job", collect)
    result = current._run_job("prices", NOW)
    assert result.state == "waiting_source"
    snapshot = current.repository.latest_price_snapshot()
    assert snapshot is not None
    assert snapshot.as_of_date == date(2026, 9, 29)
    assert snapshot.coverage.ratio == 0.5
    attempt = current.repository.daily_attempts()[0]
    assert attempt.next_retry_at_utc == NOW + timedelta(hours=1)
    assert current.repository.get_sync_run(attempt.run_id).status == "COMPLETE"  # type: ignore[union-attr]
    current.repository.close()


@pytest.mark.parametrize("phase", ["before_source", "during_source", "published", "partial"])
def test_crash_recovery_uses_published_source_facts(tmp_path: Path, phase: str) -> None:
    current = runner(tmp_path)
    current.repository.start_daily_attempt(
        DailySyncAttempt(
            "crash", "prices", NOW, date(2026, 9, 29), 1, NOW, None, "running", None, None
        )
    )
    if phase != "before_source":
        current.repository.start_sync_run(
            run_id="crash",
            source="eodhd_prices",
            started_at_utc=NOW,
            requested_start_date=date(2026, 9, 28),
            requested_end_date=date(2026, 9, 29),
            instrument_count=2,
        )
    if phase in {"published", "partial"}:
        publish_price(current.repository, "crash", already_started=True, partial=phase == "partial")
    current.repository.close()
    restarted = runner(tmp_path, now=NOW + timedelta(minutes=1))
    restarted.recover()
    attempt = restarted.repository.daily_attempts()[0]
    assert attempt.status == {"published": "complete", "partial": "waiting_source"}.get(
        phase, "interrupted"
    )
    assert (attempt.next_retry_at_utc is None) == (phase == "published")
    if phase == "during_source":
        source = restarted.repository.get_sync_run("crash")
        assert source is not None
        assert source.status == "FAILED"
    restarted.recover()
    assert len(restarted.repository.daily_attempts()) == 1
    restarted.repository.close()


def test_same_price_date_skips_but_empty_new_catalog_requires_initialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    current = runner(tmp_path)
    publish_price(current.repository, "copied")
    called: list[str] = []

    async def collect(self: daily._DailyRunner, job: DailyJob, target: date, run_id: str) -> None:
        called.append(run_id)
        publish_price(self.repository, run_id)

    monkeypatch.setattr(daily._DailyRunner, "_sync_job", collect)
    assert current._run_job("prices", NOW).state == "complete"
    assert len(called) == 1  # 有成功快照但没有 Catalog 时不能跳过初始化。
    assert current._run_job("prices", NOW + timedelta(minutes=1)).state == "complete"
    assert len(called) == 1
    current.repository.close()


def test_marker_is_preserved_and_other_sources_can_proceed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    current = runner(tmp_path)
    current.runtime.catalog_path.mkdir(parents=True)
    marker = current.runtime.catalog_path / ".catalog-writing"
    marker.write_text("interrupted")
    called: list[DailyJob] = []

    async def collect(self: daily._DailyRunner, job: DailyJob, target: date, run_id: str) -> None:
        called.append(job)
        raise ValueError("private token must not leak")

    monkeypatch.setattr(daily._DailyRunner, "_sync_job", collect)
    current.tick(NOW)
    assert called == ["economic_events", "fundamentals", "earnings"]
    assert marker.read_text() == "interrupted"
    assert all(
        "private" not in str(item.error_code) for item in current.repository.daily_attempts()
    )
    assert (
        current.repository.daily_attempts(job="prices")[0].error_code == "catalog_recovery_required"
    )
    current.repository.close()


def test_sleep_recovery_captures_real_date_without_replaying_missed_days(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed = NOW

    async def collect(self: daily._DailyRunner, job: DailyJob, target: date, run_id: str) -> None:
        capture = self.clock()
        assert target == capture.date()
        self.repository.start_sync_run(
            run_id=run_id,
            source="eodhd_economic_events",
            started_at_utc=capture,
            requested_start_date=target,
            requested_end_date=target + timedelta(days=30),
            instrument_count=0,
        )
        snapshot = EconomicEventSnapshot(
            source="eodhd_economic_events",
            as_of_date=target,
            captured_at_utc=capture,
            window_start=target,
            window_end=target + timedelta(days=30),
            country="US",
            batch=EconomicEventBatch(
                request_count=1, raw_record_count=0, duplicate_count=0, events=()
            ),
        )
        self.repository.publish_economic_events_and_complete(run_id, snapshot=snapshot)

    monkeypatch.setattr(daily._DailyRunner, "_sync_job", collect)
    current = runner(tmp_path)
    current.clock = lambda: observed
    assert current._run_job("economic_events", observed).state == "complete"
    observed = NOW + timedelta(hours=20)  # 次日凌晨, 计划日尚未变化, 真实采集日已变化。
    assert current._run_job("economic_events", observed).state == "complete"
    observed = NOW + timedelta(days=30)
    assert current._run_job("economic_events", observed).state == "complete"
    attempts = current.repository.daily_attempts()
    assert len(attempts) == 2
    assert {attempt.target_date for attempt in attempts} == {
        NOW.date(),
        observed.date(),
    }
    assert attempts[0].scheduled_at_utc == scheduled_at(observed)
    current.repository.close()


@pytest.mark.parametrize("status", [429, 503])
def test_provider_http_class_is_preserved_without_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    current = runner(tmp_path)

    async def collect(self: daily._DailyRunner, job: DailyJob, target: date, run_id: str) -> None:
        raise EodhdTemporaryHttpError(status)

    monkeypatch.setattr(daily._DailyRunner, "_sync_job", collect)
    current._run_job("economic_events", NOW)
    attempt = current.repository.daily_attempts()[0]
    assert attempt.error_code == ("rate_limited" if status == 429 else "temporary_source")
    assert attempt.next_retry_at_utc == NOW + timedelta(hours=1)
    current.repository.close()


def test_serve_finishes_active_attempt_before_stopping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    current = runner(tmp_path)
    runtime = current.runtime
    current.repository.close()
    stop = Event()

    async def collect(self: daily._DailyRunner, job: DailyJob, target: date, run_id: str) -> None:
        stop.set()
        publish_price(self.repository, run_id)

    monkeypatch.setattr(daily._DailyRunner, "_sync_job", collect)
    daily.serve_market_radar(
        runtime=runtime,
        settings=MarketRadarDailyConfig(time(6)),
        environ={},
        stop=stop,
        clock=lambda: NOW,
    )
    repository = MarketRadarRepository(runtime.database_url, read_only=True)
    attempts = repository.daily_attempts()
    assert len(attempts) == 1
    assert attempts[0].status == "complete"
    worker = repository.daily_worker()
    assert worker is not None
    assert worker.stopped_at_utc == NOW
    assert worker.active_job is None
    repository.close()


def test_running_attempt_without_retry_time_is_not_blocked(tmp_path: Path) -> None:
    current = runner(tmp_path)
    current.repository.start_daily_attempt(
        DailySyncAttempt(
            "active", "prices", NOW, expected_price_date(NOW), 1, NOW, None, "running", None, None
        )
    )
    current.active_job = "prices"
    current.heartbeat()
    status = MarketRadarQueryService(repository=current.repository, clock=lambda: NOW).sync_status()
    assert status.worker_state == "running"
    assert status.jobs[0].state == "running"
    assert status.jobs[0].next_retry_at_utc is None
    current.repository.close()


def test_before_six_recovery_uses_capture_date_then_reuses_it_for_new_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed = NOW - timedelta(hours=4)
    current = runner(tmp_path, now=observed)
    current.clock = lambda: observed
    current.environ = {"EODHD_API_TOKEN": "test"}
    monkeypatch.setattr(service, "download", lambda *_: b"[]")
    assert current._run_job("economic_events", observed).state == "complete"
    attempt = current.repository.daily_attempts()[0]
    assert attempt.scheduled_at_utc == NOW - timedelta(days=1)
    assert attempt.target_date == NOW.date()
    observed = NOW
    assert current._run_job("economic_events", observed).state == "complete"
    observed = NOW + timedelta(hours=18)
    assert current._run_job("economic_events", observed).state == "complete"
    assert len(current.repository.daily_attempts()) == 1
    status = MarketRadarQueryService(
        repository=current.repository, clock=lambda: observed
    ).sync_status()
    assert status.jobs[2].target_date == NOW.date()
    assert status.jobs[2].target_satisfied
    current.repository.close()

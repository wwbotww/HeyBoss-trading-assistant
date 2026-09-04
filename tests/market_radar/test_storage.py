"""市场雷达独立 SQLite 仓储测试。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, inspect, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from trading_assistant.market_radar.earnings import (
    EarningsCalendarEvent,
    EarningsRevisionSnapshot,
    Fy1EarningsTrend,
    calculate_earnings_revision_snapshot,
)
from trading_assistant.market_radar.fred import FredObservation
from trading_assistant.market_radar.macro import (
    RiskAppetiteComponents,
    RiskAppetitePoint,
    RiskAppetiteSnapshot,
)
from trading_assistant.market_radar.membership import (
    CurrentMarketMember,
    CurrentMarketMembership,
    CurrentMarketSectorAssignment,
    CurrentMarketSectorClassification,
)
from trading_assistant.market_radar.metrics import (
    BreadthMetric,
    CurrentBreadthSnapshot,
    MarketPriceMetrics,
    MetricValue,
    PriceCoverage,
    PriceRadarSnapshot,
)
from trading_assistant.market_radar.models import (
    EarningsCalendarEventRecord,
    EarningsMarketMemberRecord,
    EarningsRevisionSnapshotRecord,
    EarningsTrendObservationRecord,
    MacroObservationRecord,
    MarketRadarSyncRunRecord,
)
from trading_assistant.market_radar.regime import (
    ALIGNMENT_MAX_AGE_DAYS,
    NEUTRAL_BAND,
    MacroRegimePoint,
    MacroRegimeSnapshot,
    RealRateState,
)
from trading_assistant.market_radar.storage import MarketRadarRepository

STARTED = datetime(2026, 9, 3, 1, tzinfo=UTC)


def _start(repository: MarketRadarRepository, run_id: str) -> None:
    repository.start_sync_run(
        run_id=run_id,
        source="eodhd_prices",
        started_at_utc=STARTED,
        requested_start_date=date(2026, 8, 1),
        requested_end_date=date(2026, 9, 1),
        instrument_count=2,
    )


def _snapshot(
    *,
    as_of_date: date = date(2026, 9, 1),
    calculated_at: datetime = STARTED,
    spy_return: float = 0.01,
) -> PriceRadarSnapshot:
    return PriceRadarSnapshot(
        as_of_date=as_of_date,
        calculated_at_utc=calculated_at,
        coverage=PriceCoverage(eligible=1, observed=1, ratio=1),
        market=MarketPriceMetrics(
            spy_return_20=MetricValue(spy_return, "complete", 21, 21),
            spy_distance_ma_200=MetricValue(None, "insufficient_history", 21, 200),
            rsp_spy_return_20=MetricValue(None, "unavailable", 0, 21),
        ),
        sectors=(),
        stocks=(),
    )


def _membership() -> CurrentMarketMembership:
    return CurrentMarketMembership(
        source="test_current_members",
        membership_date=date(2026, 8, 31),
        members=(
            CurrentMarketMember("AAPL", "AAPL.US", "AAPL.US"),
            CurrentMarketMember("MSFT", "MSFT.US", "MSFT.US"),
        ),
    )


def _breadth_snapshot(*, calculated_at: datetime = STARTED) -> CurrentBreadthSnapshot:
    complete = BreadthMetric(0.5, "complete", 2, 2, 1, 50)
    return CurrentBreadthSnapshot(
        as_of_date=date(2026, 9, 1),
        membership_date=date(2026, 8, 31),
        membership_source="test_current_members",
        calculated_at_utc=calculated_at,
        b50=complete,
        b200=replace(complete, history_required=200),
        ad10=replace(complete, history_required=11),
        nhnl=replace(complete, history_required=252),
    )


def _risk_snapshot(*, calculated_at: datetime = STARTED) -> RiskAppetiteSnapshot:
    day = date(2026, 9, 1)
    point = RiskAppetitePoint(day=day, score=0.6, credit_z=1, volatility_z=0)
    return RiskAppetiteSnapshot(
        as_of_date=day,
        calculated_at_utc=calculated_at,
        validity="complete",
        observations=504,
        required=504,
        credit_source="etf_proxy",
        price_source="eodhd_nt_catalog",
        components=RiskAppetiteComponents(
            day=day,
            hyg_close=80,
            lqd_close=100,
            vix_close=20,
            vix3m_close=22,
            credit_log_change_20=0.01,
            volatility_term_log=-0.09,
        ),
        current=point,
        trajectory=(point,),
    )


def _fred_observations(*, value: float = 1.72) -> tuple[FredObservation, ...]:
    return (
        FredObservation(
            series_id="DFII10",
            observation_date=date(2026, 8, 31),
            value=value - 0.02,
            realtime_start=date(2026, 9, 3),
            realtime_end=date(2026, 9, 3),
        ),
        FredObservation(
            series_id="DFII10",
            observation_date=date(2026, 9, 1),
            value=value,
            realtime_start=date(2026, 9, 3),
            realtime_end=date(2026, 9, 3),
        ),
    )


def _regime_snapshot(
    *,
    calculated_at: datetime = STARTED,
    real_rate_level: float = 1.72,
) -> MacroRegimeSnapshot:
    day = date(2026, 9, 1)
    point = MacroRegimePoint(
        day=day,
        real_rate_observation_date=day,
        real_rate_level_percent=real_rate_level,
        real_rate_change_20_percentage_points=-0.15,
        real_rate_pressure_z=-1,
        real_rate_percentile_3y=0.4,
        risk_appetite_score=0.6,
        credit_z=1,
        volatility_z=0,
        regime="easing_risk_on",
        regime_label="宽松型 Risk-on",
    )
    return MacroRegimeSnapshot(
        as_of_date=day,
        calculated_at_utc=calculated_at,
        validity="complete",
        neutral_band=NEUTRAL_BAND,
        alignment_max_age_days=ALIGNMENT_MAX_AGE_DAYS,
        real_rate_source="fred_dfii10",
        real_rate_vintage="current",
        credit_source="etf_proxy",
        price_source="eodhd_nt_catalog",
        risk_appetite_as_of_date=day,
        real_rate=RealRateState(
            series_id="DFII10",
            latest_observation_date=day,
            level_percent=real_rate_level,
            change_20_percentage_points=-0.15,
            pressure_z=-1,
            percentile_3y=0.4,
            validity="complete",
            observations=504,
            required=504,
        ),
        current=point,
        trajectory=(point,),
        duration_observations=1,
    )


def _earnings_trends(
    *,
    aapl_current: float = 7.5,
) -> tuple[Fy1EarningsTrend, ...]:
    return (
        Fy1EarningsTrend(
            instrument_id="AAPL.US",
            fiscal_period_end=date(2027, 9, 30),
            eps_current=aapl_current,
            eps_30_days_ago=7.25,
            analyst_count=30,
            revisions_up_30_days=5,
            revisions_down_30_days=None,
        ),
        Fy1EarningsTrend(
            instrument_id="MSFT.US",
            fiscal_period_end=date(2027, 6, 30),
            eps_current=None,
            eps_30_days_ago=12.5,
            analyst_count=0,
            revisions_up_30_days=None,
            revisions_down_30_days=2,
        ),
    )


def _earnings_events() -> tuple[EarningsCalendarEvent, ...]:
    return (
        EarningsCalendarEvent(
            instrument_id="AAPL.US",
            fiscal_period_end=date(2026, 6, 30),
            report_date=date(2026, 7, 30),
            session="after_market",
            currency="USD",
            actual_eps=1.25,
            estimated_eps=1.10,
        ),
        EarningsCalendarEvent(
            instrument_id="MSFT.US",
            fiscal_period_end=date(2026, 9, 30),
            report_date=date(2026, 10, 28),
            session="unknown",
            currency="USD",
            actual_eps=None,
            estimated_eps=None,
        ),
    )


def _earnings_classification() -> CurrentMarketSectorClassification:
    return CurrentMarketSectorClassification(
        source="test_sector_source",
        requested_member_count=2,
        source_record_count=2,
        assignments=(
            CurrentMarketSectorAssignment("AAPL.US", "information_technology"),
            CurrentMarketSectorAssignment("MSFT.US", "information_technology"),
        ),
    )


def _earnings_snapshot(
    *,
    calculated_at: datetime = STARTED,
    trends: tuple[Fy1EarningsTrend, ...] | None = None,
) -> EarningsRevisionSnapshot:
    return calculate_earnings_revision_snapshot(
        as_of_date=calculated_at.date(),
        calculated_at_utc=calculated_at,
        watchlist=("AAPL.US", "MSFT.US"),
        membership=_membership(),
        classification=_earnings_classification(),
        sector_ids=("information_technology", "financials"),
        trends=_earnings_trends() if trends is None else trends,
    )


def test_schema_is_independent_and_complete_run_is_readable(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path}/market-radar.db"
    repository = MarketRadarRepository(database_url)
    repository.create_schema()
    _start(repository, "run-complete")
    snapshot = _snapshot(calculated_at=STARTED + timedelta(seconds=30))
    repository.publish_price_snapshot_and_complete(
        "run-complete",
        snapshot=snapshot,
        completed_at_utc=STARTED + timedelta(minutes=1),
        instruments_processed=2,
        bars_fetched=80,
        bars_written=80,
    )

    run = repository.get_sync_run("run-complete")
    assert run is not None
    assert run.status == "COMPLETE"
    assert run.started_at_utc == STARTED
    assert run.completed_at_utc == STARTED + timedelta(minutes=1)
    assert run.instrument_count == 2
    assert run.instruments_processed == 2
    assert run.bars_fetched == 80
    assert run.bars_written == 80
    assert run.error_summary is None
    assert repository.latest_complete_run() == run
    assert repository.latest_price_snapshot() == snapshot
    assert repository.get_sync_run("missing") is None
    repository.healthcheck()
    repository.close()

    engine = create_engine(database_url)
    assert inspect(engine).get_table_names() == [
        "current_breadth_snapshots",
        "current_market_members",
        "earnings_calendar_events",
        "earnings_market_members",
        "earnings_revision_snapshots",
        "earnings_trend_observations",
        "macro_observations",
        "macro_regime_snapshots",
        "price_snapshots",
        "risk_appetite_snapshots",
        "sync_runs",
    ]
    engine.dispose()


def test_earnings_bundle_persists_nulls_and_completes_run(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path}/earnings.db"
    repository = MarketRadarRepository(database_url)
    repository.create_schema()
    _start(repository, "earnings")
    trends = _earnings_trends()
    events = _earnings_events()
    snapshot = _earnings_snapshot(trends=trends)

    repository.publish_earnings_bundle_and_complete(
        "earnings",
        membership=_membership(),
        classification=_earnings_classification(),
        trends=trends,
        events=events,
        snapshot=snapshot,
        ingested_at_utc=STARTED,
        completed_at_utc=STARTED + timedelta(minutes=1),
        instruments_processed=2,
    )

    assert repository.latest_earnings_revision_snapshot() == snapshot
    run = repository.get_sync_run("earnings")
    assert run is not None
    assert run.status == "COMPLETE"
    assert run.instruments_processed == 2
    assert run.bars_fetched == 0
    assert run.bars_written == 0

    engine = create_engine(database_url)
    with Session(engine) as session:
        stored_trends = session.scalars(
            select(EarningsTrendObservationRecord).order_by(
                EarningsTrendObservationRecord.instrument_id
            )
        ).all()
        stored_events = session.scalars(
            select(EarningsCalendarEventRecord).order_by(EarningsCalendarEventRecord.instrument_id)
        ).all()
        stored_members = session.scalars(
            select(EarningsMarketMemberRecord).order_by(EarningsMarketMemberRecord.instrument_id)
        ).all()
    engine.dispose()
    assert [item.instrument_id for item in stored_trends] == ["AAPL.US", "MSFT.US"]
    assert stored_trends[1].eps_current is None
    assert stored_trends[0].available_at_utc is None
    assert stored_events[1].estimated_eps is None
    assert stored_events[0].available_at_utc is None
    assert [item.instrument_id for item in stored_members] == ["AAPL.US", "MSFT.US"]
    assert stored_members[0].sector == "information_technology"
    assert stored_members[0].classification_source == "test_sector_source"
    repository.close()


def test_earnings_same_day_replaces_and_later_day_appends(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path}/earnings-history.db"
    repository = MarketRadarRepository(database_url)
    repository.create_schema()
    old_trends = _earnings_trends()
    _start(repository, "day-one-old")
    repository.publish_earnings_bundle_and_complete(
        "day-one-old",
        membership=_membership(),
        classification=_earnings_classification(),
        trends=old_trends,
        events=_earnings_events(),
        snapshot=_earnings_snapshot(trends=old_trends),
        ingested_at_utc=STARTED,
        completed_at_utc=STARTED + timedelta(minutes=1),
        instruments_processed=2,
    )

    replacement_time = STARTED + timedelta(hours=1)
    replacement_trends = _earnings_trends(aapl_current=8.0)
    _start(repository, "day-one-new")
    repository.publish_earnings_bundle_and_complete(
        "day-one-new",
        membership=_membership(),
        classification=_earnings_classification(),
        trends=replacement_trends,
        events=(_earnings_events()[0],),
        snapshot=_earnings_snapshot(
            calculated_at=replacement_time,
            trends=replacement_trends,
        ),
        ingested_at_utc=replacement_time,
        completed_at_utc=replacement_time + timedelta(minutes=1),
        instruments_processed=2,
    )

    next_day = STARTED + timedelta(days=1)
    _start(repository, "day-two")
    repository.publish_earnings_bundle_and_complete(
        "day-two",
        membership=_membership(),
        classification=_earnings_classification(),
        trends=replacement_trends,
        events=_earnings_events(),
        snapshot=_earnings_snapshot(calculated_at=next_day, trends=replacement_trends),
        ingested_at_utc=next_day,
        completed_at_utc=next_day + timedelta(minutes=1),
        instruments_processed=2,
    )

    engine = create_engine(database_url)
    with Session(engine) as session:
        trend_records = session.scalars(select(EarningsTrendObservationRecord)).all()
        event_records = session.scalars(select(EarningsCalendarEventRecord)).all()
        snapshots = session.scalars(select(EarningsRevisionSnapshotRecord)).all()
        member_records = session.scalars(select(EarningsMarketMemberRecord)).all()
    engine.dispose()
    assert len(trend_records) == 4
    assert len(event_records) == 3
    assert len(snapshots) == 2
    assert len(member_records) == 4
    day_one_aapl = next(
        item
        for item in trend_records
        if item.as_of_date == STARTED.date() and item.instrument_id == "AAPL.US"
    )
    assert day_one_aapl.eps_current == 8.0
    assert day_one_aapl.run_id == "day-one-new"
    assert repository.latest_earnings_revision_snapshot().as_of_date == next_day.date()  # type: ignore[union-attr]
    repository.close()


def test_earnings_publication_and_completion_roll_back_together(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path}/earnings-atomic.db"
    repository = MarketRadarRepository(database_url)
    repository.create_schema()
    old_trends = _earnings_trends()
    old_snapshot = _earnings_snapshot(trends=old_trends)
    _start(repository, "old-earnings")
    repository.publish_earnings_bundle_and_complete(
        "old-earnings",
        membership=_membership(),
        classification=_earnings_classification(),
        trends=old_trends,
        events=_earnings_events(),
        snapshot=old_snapshot,
        ingested_at_utc=STARTED,
        completed_at_utc=STARTED + timedelta(minutes=1),
        instruments_processed=2,
    )
    _start(repository, "new-earnings")

    def fail_completion(
        _mapper: object,
        _connection: object,
        target: MarketRadarSyncRunRecord,
    ) -> None:
        if target.run_id == "new-earnings" and target.status == "COMPLETE":
            raise RuntimeError("simulated earnings completion failure")

    replacement_time = STARTED + timedelta(hours=1)
    replacement_trends = _earnings_trends(aapl_current=8.0)
    event.listen(MarketRadarSyncRunRecord, "before_update", fail_completion)
    try:
        with pytest.raises(RuntimeError, match="simulated"):
            repository.publish_earnings_bundle_and_complete(
                "new-earnings",
                membership=_membership(),
                classification=_earnings_classification(),
                trends=replacement_trends,
                events=(_earnings_events()[0],),
                snapshot=_earnings_snapshot(
                    calculated_at=replacement_time,
                    trends=replacement_trends,
                ),
                ingested_at_utc=replacement_time,
                completed_at_utc=replacement_time + timedelta(minutes=1),
                instruments_processed=2,
            )
    finally:
        event.remove(MarketRadarSyncRunRecord, "before_update", fail_completion)

    assert repository.latest_earnings_revision_snapshot() == old_snapshot
    new_run = repository.get_sync_run("new-earnings")
    assert new_run is not None
    assert new_run.status == "RUNNING"
    engine = create_engine(database_url)
    with Session(engine) as session:
        run_ids = set(session.scalars(select(EarningsTrendObservationRecord.run_id)))
        member_run_ids = set(session.scalars(select(EarningsMarketMemberRecord.run_id)))
    engine.dispose()
    assert run_ids == {"old-earnings"}
    assert member_run_ids == {"old-earnings"}
    repository.close()


def test_failed_run_does_not_replace_latest_complete(tmp_path: Path) -> None:
    repository = MarketRadarRepository(f"sqlite:///{tmp_path}/runs.db")
    repository.create_schema()
    assert repository.latest_complete_run() is None
    _start(repository, "complete")
    snapshot = _snapshot()
    repository.publish_price_snapshot_and_complete(
        "complete",
        snapshot=snapshot,
        completed_at_utc=STARTED + timedelta(minutes=1),
        instruments_processed=2,
        bars_fetched=20,
        bars_written=20,
    )
    _start(repository, "failed")
    repository.fail_sync_run(
        "failed",
        completed_at_utc=STARTED + timedelta(minutes=2),
        error_summary="data_quality",
        instruments_processed=1,
        bars_fetched=10,
        bars_written=0,
    )

    failed = repository.get_sync_run("failed")
    assert failed is not None
    assert failed.status == "FAILED"
    assert failed.error_summary == "data_quality"
    assert repository.latest_complete_run().run_id == "complete"  # type: ignore[union-attr]
    assert repository.latest_price_snapshot() == snapshot
    repository.close()


def test_state_transitions_are_atomic(tmp_path: Path) -> None:
    repository = MarketRadarRepository(f"sqlite:///{tmp_path}/state.db")
    repository.create_schema()
    with pytest.raises(LookupError, match="not found"):
        repository.publish_price_snapshot_and_complete(
            "missing",
            snapshot=_snapshot(),
            completed_at_utc=STARTED,
            instruments_processed=0,
            bars_fetched=0,
            bars_written=0,
        )
    _start(repository, "run")
    with pytest.raises(IntegrityError):
        _start(repository, "run")
    repository.fail_sync_run(
        "run",
        completed_at_utc=STARTED + timedelta(minutes=1),
        error_summary="ConnectionError",
    )
    with pytest.raises(LookupError, match="not found"):
        repository.publish_price_snapshot_and_complete(
            "run",
            snapshot=_snapshot(),
            completed_at_utc=STARTED + timedelta(minutes=2),
            instruments_processed=0,
            bars_fetched=0,
            bars_written=0,
        )
    repository.close()


def test_snapshot_replacement_and_completion_roll_back_together(tmp_path: Path) -> None:
    """状态更新失败时同日期旧快照必须仍完整可读。"""
    repository = MarketRadarRepository(f"sqlite:///{tmp_path}/atomic.db")
    repository.create_schema()
    _start(repository, "old")
    old_snapshot = _snapshot()
    repository.publish_price_snapshot_and_complete(
        "old",
        snapshot=old_snapshot,
        completed_at_utc=STARTED + timedelta(minutes=1),
        instruments_processed=2,
        bars_fetched=20,
        bars_written=20,
    )
    _start(repository, "new")

    def fail_completion(
        _mapper: object,
        _connection: object,
        target: MarketRadarSyncRunRecord,
    ) -> None:
        if target.run_id == "new" and target.status == "COMPLETE":
            raise RuntimeError("simulated completion failure")

    event.listen(MarketRadarSyncRunRecord, "before_update", fail_completion)
    try:
        with pytest.raises(RuntimeError, match="simulated"):
            repository.publish_price_snapshot_and_complete(
                "new",
                snapshot=_snapshot(
                    calculated_at=STARTED + timedelta(minutes=2),
                    spy_return=0.02,
                ),
                completed_at_utc=STARTED + timedelta(minutes=3),
                instruments_processed=2,
                bars_fetched=20,
                bars_written=20,
            )
    finally:
        event.remove(MarketRadarSyncRunRecord, "before_update", fail_completion)

    assert repository.latest_price_snapshot() == old_snapshot
    new_run = repository.get_sync_run("new")
    assert new_run is not None
    assert new_run.status == "RUNNING"
    repository.close()


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"run_id": "", "source": "eodhd_prices", "instrument_count": 1}, "run_id"),
        ({"run_id": "run", "source": "", "instrument_count": 1}, "source"),
        ({"run_id": "run", "source": "eodhd_prices", "instrument_count": 0}, "one"),
    ],
)
def test_start_validation(
    tmp_path: Path,
    kwargs: dict[str, str | int],
    message: str,
) -> None:
    repository = MarketRadarRepository(f"sqlite:///{tmp_path}/validation.db")
    repository.create_schema()
    with pytest.raises(ValueError, match=message):
        repository.start_sync_run(
            **kwargs,  # type: ignore[arg-type]
            started_at_utc=STARTED,
            requested_start_date=None,
            requested_end_date=None,
        )
    repository.close()


def test_timestamp_date_and_count_validation(tmp_path: Path) -> None:
    repository = MarketRadarRepository(f"sqlite:///{tmp_path}/invalid.db")
    repository.create_schema()
    with pytest.raises(ValueError, match="timezone-aware"):
        repository.start_sync_run(
            run_id="naive",
            source="eodhd_prices",
            started_at_utc=datetime(2026, 9, 3),
            requested_start_date=None,
            requested_end_date=None,
            instrument_count=1,
        )
    with pytest.raises(ValueError, match="earlier"):
        repository.start_sync_run(
            run_id="dates",
            source="eodhd_prices",
            started_at_utc=STARTED,
            requested_start_date=date(2026, 9, 1),
            requested_end_date=date(2026, 9, 1),
            instrument_count=1,
        )
    _start(repository, "counts")
    with pytest.raises(ValueError, match="negative"):
        repository.publish_price_snapshot_and_complete(
            "counts",
            snapshot=_snapshot(),
            completed_at_utc=STARTED,
            instruments_processed=-1,
            bars_fetched=0,
            bars_written=0,
        )
    with pytest.raises(ValueError, match="empty"):
        repository.fail_sync_run(
            "counts",
            completed_at_utc=STARTED,
            error_summary=" ",
        )
    repository.close()


def test_read_only_repository_rejects_writes(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path}/readonly.db"
    writer = MarketRadarRepository(database_url)
    writer.create_schema()
    writer.close()

    reader = MarketRadarRepository(database_url, read_only=True)
    reader.healthcheck()
    with pytest.raises(OperationalError, match="readonly"):
        _start(reader, "blocked")
    reader.close()


def test_read_only_missing_database_is_not_created(tmp_path: Path) -> None:
    path = tmp_path / "missing" / "market-radar.db"
    reader = MarketRadarRepository(f"sqlite:///{path}", read_only=True)
    assert not path.exists()
    with pytest.raises(OperationalError):
        reader.healthcheck()
    assert not path.exists()
    reader.close()


def test_repository_rejects_unsupported_urls() -> None:
    with pytest.raises(ValueError, match="SQLite"):
        MarketRadarRepository("postgresql://localhost/market")
    with pytest.raises(ValueError, match="in-memory"):
        MarketRadarRepository("sqlite:///:memory:", read_only=True)


def test_breadth_membership_snapshot_and_completion_are_published_atomically(
    tmp_path: Path,
) -> None:
    repository = MarketRadarRepository(f"sqlite:///{tmp_path}/breadth.db")
    repository.create_schema()
    _start(repository, "breadth")
    membership = _membership()
    snapshot = _breadth_snapshot(calculated_at=STARTED + timedelta(seconds=30))

    repository.publish_current_breadth_and_complete(
        "breadth",
        membership=membership,
        snapshot=snapshot,
        completed_at_utc=STARTED + timedelta(minutes=1),
        instruments_processed=2,
        bars_fetched=1_000,
        bars_written=1_000,
    )

    assert repository.latest_current_membership() == membership
    assert repository.latest_current_breadth_snapshot() == snapshot
    run = repository.get_sync_run("breadth")
    assert run is not None
    assert run.status == "COMPLETE"
    repository.close()


def test_failed_breadth_run_does_not_replace_last_complete_snapshot(tmp_path: Path) -> None:
    repository = MarketRadarRepository(f"sqlite:///{tmp_path}/breadth-failure.db")
    repository.create_schema()
    _start(repository, "complete")
    membership = _membership()
    snapshot = _breadth_snapshot()
    repository.publish_current_breadth_and_complete(
        "complete",
        membership=membership,
        snapshot=snapshot,
        completed_at_utc=STARTED + timedelta(minutes=1),
        instruments_processed=2,
        bars_fetched=1_000,
        bars_written=1_000,
    )
    _start(repository, "failed")
    repository.fail_sync_run(
        "failed",
        completed_at_utc=STARTED + timedelta(minutes=2),
        error_summary="data_quality",
    )

    assert repository.latest_current_membership() == membership
    assert repository.latest_current_breadth_snapshot() == snapshot
    repository.close()


def test_breadth_publication_rejects_mismatched_membership(tmp_path: Path) -> None:
    repository = MarketRadarRepository(f"sqlite:///{tmp_path}/breadth-invalid.db")
    repository.create_schema()
    _start(repository, "breadth")
    with pytest.raises(ValueError, match="does not match"):
        repository.publish_current_breadth_and_complete(
            "breadth",
            membership=replace(_membership(), source="other_source"),
            snapshot=_breadth_snapshot(),
            completed_at_utc=STARTED,
            instruments_processed=2,
            bars_fetched=0,
            bars_written=0,
        )
    assert repository.get_sync_run("breadth").status == "RUNNING"  # type: ignore[union-attr]
    assert repository.latest_current_breadth_snapshot() is None
    repository.close()


def test_macro_bundle_and_completion_are_published_atomically(tmp_path: Path) -> None:
    repository = MarketRadarRepository(f"sqlite:///{tmp_path}/risk.db")
    repository.create_schema()
    _start(repository, "risk")
    risk = _risk_snapshot(calculated_at=STARTED + timedelta(seconds=30))
    regime = _regime_snapshot(calculated_at=STARTED + timedelta(seconds=30))

    repository.publish_macro_bundle_and_complete(
        "risk",
        risk_snapshot=risk,
        observations=_fred_observations(),
        regime_snapshot=regime,
        ingested_at_utc=STARTED + timedelta(seconds=20),
        completed_at_utc=STARTED + timedelta(minutes=1),
        instruments_processed=4,
        bars_fetched=4_000,
        bars_written=4_000,
    )

    assert repository.latest_risk_appetite_snapshot() == risk
    assert repository.latest_macro_regime_snapshot() == regime
    run = repository.get_sync_run("risk")
    assert run is not None
    assert run.status == "COMPLETE"
    assert run.instruments_processed == 4
    repository.close()


def test_failed_macro_run_does_not_replace_last_complete_snapshot(tmp_path: Path) -> None:
    repository = MarketRadarRepository(f"sqlite:///{tmp_path}/risk-failure.db")
    repository.create_schema()
    _start(repository, "complete")
    risk = _risk_snapshot()
    regime = _regime_snapshot()
    repository.publish_macro_bundle_and_complete(
        "complete",
        risk_snapshot=risk,
        observations=_fred_observations(),
        regime_snapshot=regime,
        ingested_at_utc=STARTED,
        completed_at_utc=STARTED + timedelta(minutes=1),
        instruments_processed=4,
        bars_fetched=4_000,
        bars_written=4_000,
    )
    _start(repository, "failed")
    repository.fail_sync_run(
        "failed",
        completed_at_utc=STARTED + timedelta(minutes=2),
        error_summary="data_quality",
    )

    assert repository.latest_risk_appetite_snapshot() == risk
    assert repository.latest_macro_regime_snapshot() == regime
    repository.close()


def test_macro_current_revision_upsert_and_completion_roll_back_together(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite:///{tmp_path}/macro-atomic.db"
    repository = MarketRadarRepository(database_url)
    repository.create_schema()
    _start(repository, "old")
    old_risk = _risk_snapshot()
    old_regime = _regime_snapshot()
    repository.publish_macro_bundle_and_complete(
        "old",
        risk_snapshot=old_risk,
        observations=_fred_observations(value=1.72),
        regime_snapshot=old_regime,
        ingested_at_utc=STARTED,
        completed_at_utc=STARTED + timedelta(minutes=1),
        instruments_processed=4,
        bars_fetched=4_000,
        bars_written=4_000,
    )
    _start(repository, "new")

    def fail_completion(
        _mapper: object,
        _connection: object,
        target: MarketRadarSyncRunRecord,
    ) -> None:
        if target.run_id == "new" and target.status == "COMPLETE":
            raise RuntimeError("simulated macro completion failure")

    event.listen(MarketRadarSyncRunRecord, "before_update", fail_completion)
    try:
        with pytest.raises(RuntimeError, match="simulated"):
            repository.publish_macro_bundle_and_complete(
                "new",
                risk_snapshot=replace(
                    old_risk,
                    calculated_at_utc=STARTED + timedelta(minutes=2),
                ),
                observations=_fred_observations(value=1.80),
                regime_snapshot=replace(
                    _regime_snapshot(real_rate_level=1.80),
                    calculated_at_utc=STARTED + timedelta(minutes=2),
                ),
                ingested_at_utc=STARTED + timedelta(minutes=2),
                completed_at_utc=STARTED + timedelta(minutes=3),
                instruments_processed=4,
                bars_fetched=4_000,
                bars_written=4_000,
            )
    finally:
        event.remove(MarketRadarSyncRunRecord, "before_update", fail_completion)

    assert repository.latest_risk_appetite_snapshot() == old_risk
    assert repository.latest_macro_regime_snapshot() == old_regime
    engine = create_engine(database_url)
    with Session(engine) as session:
        value = session.scalar(
            select(MacroObservationRecord.value).where(
                MacroObservationRecord.observation_date == date(2026, 9, 1)
            )
        )
    engine.dispose()
    assert value == 1.72
    run = repository.get_sync_run("new")
    assert run is not None
    assert run.status == "RUNNING"
    repository.close()

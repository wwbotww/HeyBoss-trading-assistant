"""市场雷达独立 SQLite 仓储测试。"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, inspect, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from tests.market_radar.test_economic_events import ECONOMIC_AT, economic_event, economic_snapshot
from tests.market_radar.test_eodhd_fundamentals import observation
from tests.market_radar.test_fundamentals import CALCULATED, changed, snapshot
from trading_assistant.market_radar.earnings import (
    EarningsCalendarEvent,
    EarningsRevisionSnapshot,
    Fy1EarningsTrend,
    calculate_earnings_revision_snapshot,
)
from trading_assistant.market_radar.economic_events import EconomicEventBatch
from trading_assistant.market_radar.fred import FredObservation
from trading_assistant.market_radar.fundamentals import (
    FundamentalObservation,
    calculate_fundamental_snapshot,
)
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
    EconomicEventSnapshotRecord,
    FundamentalObservationRecord,
    FundamentalSnapshotRecord,
    MacroObservationRecord,
    MarketRadarBase,
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


def _publish_event_batch(
    repository: MarketRadarRepository,
    run_id: str,
    at: datetime,
    events: tuple[EarningsCalendarEvent, ...],
) -> None:
    """用真实发布入口生成合法的日历读取测试批次。"""
    repository.start_sync_run(
        run_id=run_id,
        source="eodhd_earnings",
        started_at_utc=at,
        requested_start_date=at.date() - timedelta(days=365),
        requested_end_date=at.date() + timedelta(days=60),
        instrument_count=3,
    )
    repository.publish_earnings_bundle_and_complete(
        run_id,
        membership=_membership(),
        classification=_earnings_classification(),
        trends=_earnings_trends(),
        events=events,
        snapshot=_earnings_snapshot(calculated_at=at),
        ingested_at_utc=at,
        completed_at_utc=at,
        instruments_processed=3,
    )


def test_event_reader_replacements_empty_batches_and_read_only_bytes(tmp_path: Path) -> None:
    path = tmp_path / "events.db"
    url = f"sqlite:///{path}"
    writer = MarketRadarRepository(url)
    writer.create_schema()
    _publish_event_batch(writer, "old", STARTED, _earnings_events())
    reader = MarketRadarRepository(url, read_only=True)
    original = reader.latest_earnings_event_batch()
    assert original is not None
    assert original.events == _earnings_events()
    assert original.watchlist_count == 2  # 运行的并集是三只, 不能当作观察池数量。
    replacement = (
        replace(
            _earnings_events()[0],
            report_date=date(2026, 9, 5),
            currency=None,
            actual_eps=0,
            estimated_eps=-0.1,
        ),
    )
    _publish_event_batch(writer, "moved", STARTED + timedelta(minutes=1), replacement)
    updated = reader.latest_earnings_event_batch()
    assert updated is not None
    assert updated.events == replacement
    _publish_event_batch(writer, "next-empty", STARTED + timedelta(days=1), ())
    before = path.read_bytes()
    empty = reader.latest_earnings_event_batch()
    assert empty is not None
    assert empty.events == ()
    assert empty.as_of_date == (STARTED + timedelta(days=1)).date()
    assert path.read_bytes() == before
    reader.close()
    writer.close()


def test_event_reader_uses_one_snapshot_during_concurrent_same_day_publish(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path}/concurrent.db"
    writer = MarketRadarRepository(url)
    writer.create_schema()
    with writer._engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA journal_mode=WAL")
    _publish_event_batch(writer, "old", STARTED, _earnings_events())
    reader = MarketRadarRepository(url, read_only=True)
    statements: list[str] = []

    def publish_during_read(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if statement.startswith("SELECT"):
            statements.append(statement)
            _publish_event_batch(writer, "new", STARTED + timedelta(minutes=1), ())

    event.listen(reader._engine, "after_cursor_execute", publish_during_read)
    try:
        batch = reader.latest_earnings_event_batch()
        assert batch is not None
        assert batch.events == _earnings_events()
        assert batch.captured_at_utc == STARTED
        assert len(statements) == 1
    finally:
        event.remove(reader._engine, "after_cursor_execute", publish_during_read)
    newest = reader.latest_earnings_event_batch()
    assert newest is not None
    assert newest.events == ()
    reader.close()
    writer.close()


@pytest.mark.parametrize(
    "case",
    [
        "payload",
        "snapshot_date",
        "snapshot_time",
        "source",
        "started_date",
        "backwards",
        "completed_at",
        "start_range",
        "end_range",
        "reversed_range",
        "count",
        "processed",
        "bars",
        "error",
        "orphan",
        "incomplete",
        "row_run",
        "row_range",
        "available_at",
        "ingested_date",
        "ingested_time",
        "session",
        "currency",
        "number",
        "scope",
        "bad_json",
        "bad_date",
        "bad_number",
    ],
)
def test_event_reader_rejects_corrupt_latest_batch(tmp_path: Path, case: str) -> None:
    url = f"sqlite:///{tmp_path}/corrupt.db"
    writer = MarketRadarRepository(url)
    writer.create_schema()
    _publish_event_batch(writer, "run", STARTED, _earnings_events())
    with Session(writer._engine) as session, session.begin():
        row = session.scalar(select(EarningsRevisionSnapshotRecord))
        run = session.get(MarketRadarSyncRunRecord, "run")
        item = session.scalar(select(EarningsCalendarEventRecord))
        assert row is not None
        assert run is not None
        assert item is not None
        if case == "payload":
            row.payload_json = {}
        elif case == "snapshot_date":
            row.as_of_date += timedelta(days=1)
        elif case == "snapshot_time":
            row.calculated_at_utc += timedelta(seconds=1)
        elif case == "source":
            run.source = "wrong"
        elif case == "started_date":
            run.started_at_utc -= timedelta(days=1)
        elif case == "backwards":
            run.started_at_utc += timedelta(seconds=1)
        elif case == "completed_at":
            run.completed_at_utc = None
        elif case == "start_range":
            run.requested_start_date = None
        elif case == "end_range":
            run.requested_end_date = None
        elif case == "reversed_range":
            run.requested_end_date = STARTED.date() - timedelta(days=366)
        elif case == "count":
            run.instrument_count = 1
        elif case == "processed":
            run.instruments_processed = 1
        elif case == "bars":
            run.bars_written = 1
        elif case == "error":
            run.error_summary = "private error"
        elif case == "orphan":
            row.run_id = "absent"
        elif case == "incomplete":
            run.status = "RUNNING"
        elif case == "row_run":
            item.run_id = "another-run"
        elif case == "row_range":
            item.report_date += timedelta(days=500)
        elif case == "available_at":
            item.available_at_utc = STARTED
        elif case == "ingested_date":
            item.ingested_at_utc -= timedelta(days=1)
        elif case == "ingested_time":
            item.ingested_at_utc += timedelta(seconds=1)
        elif case == "session":
            item.session = "09:30"
        elif case == "currency":
            item.currency = " USD "
        elif case == "number":
            item.actual_eps = float("inf")
        elif case == "scope":
            small = replace(
                _earnings_snapshot(),
                watchlist=replace(
                    _earnings_snapshot().watchlist,
                    eligible=1,
                    observed=1,
                    coverage_ratio=1,
                    validity="complete",
                ),
            )
            row.payload_json = small.to_payload()
    with writer._engine.begin() as connection:
        if case == "bad_json":
            connection.exec_driver_sql(
                "UPDATE earnings_revision_snapshots SET payload_json='broken'"
            )
        elif case == "bad_date":
            connection.exec_driver_sql(
                "UPDATE earnings_calendar_events SET fiscal_period_end='bad'"
            )
        elif case == "bad_number":
            connection.exec_driver_sql("UPDATE earnings_calendar_events SET estimated_eps='bad'")
    reader = MarketRadarRepository(url, read_only=True)
    try:
        if case == "incomplete":
            assert reader.latest_earnings_event_batch() is None
        else:
            with pytest.raises(RuntimeError, match=r"invalid|inconsistent|no run"):
                reader.latest_earnings_event_batch()
    finally:
        reader.close()
        writer.close()


@pytest.mark.parametrize(
    "case", ["old_schema", "empty", "broken_table", "missing_run", "missing_events"]
)
def test_event_reader_does_not_migrate_or_hide_missing_tables(tmp_path: Path, case: str) -> None:
    path = tmp_path / "old.db"
    url = f"sqlite:///{path}"
    writer = MarketRadarRepository(url)
    writer.create_schema()
    table = {
        "old_schema": "earnings_revision_snapshots",
        "broken_table": "earnings_revision_snapshots",
        "missing_run": "sync_runs",
        "missing_events": "earnings_calendar_events",
    }.get(case)
    if table is not None:
        MarketRadarBase.metadata.tables[table].drop(writer._engine)
    if case == "broken_table":
        with writer._engine.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE earnings_revision_snapshots (bad TEXT)")
    writer.close()
    before = path.read_bytes()
    reader = MarketRadarRepository(url, read_only=True)
    try:
        if case in {"old_schema", "empty"}:
            assert reader.latest_earnings_event_batch() is None
        else:
            with pytest.raises(OperationalError):
                reader.latest_earnings_event_batch()
        assert path.read_bytes() == before
    finally:
        reader.close()


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
        "economic_event_snapshots",
        "fundamental_observations",
        "fundamental_snapshots",
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


def test_old_five_table_database_initializes_without_republishing(tmp_path: Path) -> None:
    """模拟正式旧库增表, 保留已有价格/宽度及审计, 不伪造新源 COMPLETE。"""
    url = f"sqlite:///{tmp_path}/old-market.db"
    old_names = {
        "sync_runs",
        "price_snapshots",
        "current_market_members",
        "current_breadth_snapshots",
        "risk_appetite_snapshots",
    }
    engine = create_engine(url)
    old_tables = [
        table for table in MarketRadarBase.metadata.sorted_tables if table.name in old_names
    ]
    MarketRadarBase.metadata.create_all(engine, tables=old_tables)
    repository = MarketRadarRepository(url)
    _start(repository, "old-price")
    repository.publish_price_snapshot_and_complete(
        "old-price",
        snapshot=_snapshot(),
        completed_at_utc=STARTED,
        instruments_processed=2,
        bars_fetched=80,
        bars_written=80,
    )
    _start(repository, "old-breadth")
    repository.publish_current_breadth_and_complete(
        "old-breadth",
        membership=_membership(),
        snapshot=_breadth_snapshot(),
        completed_at_utc=STARTED,
        instruments_processed=2,
        bars_fetched=1_000,
        bars_written=1_000,
    )
    assert set(inspect(engine).get_table_names()) == old_names
    with engine.connect() as connection:
        before = {table.name: connection.execute(select(table)).all() for table in old_tables}

    for _ in range(2):
        repository.create_schema()
        assert set(inspect(engine).get_table_names()) == set(MarketRadarBase.metadata.tables)
        with engine.connect() as connection:
            assert {
                table.name: connection.execute(select(table)).all() for table in old_tables
            } == (before)
        assert repository.latest_price_snapshot() == _snapshot()
        assert repository.latest_current_breadth_snapshot() == _breadth_snapshot()
        assert repository.latest_macro_bundle() is None
        assert repository.latest_earnings_revision_snapshot() is None
        assert repository.latest_earnings_event_batch() is None
        assert repository.latest_fundamental_snapshot() is None
        assert repository.latest_economic_event_snapshot() is None
    repository.close()
    engine.dispose()


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


def _start_fundamental(repository: MarketRadarRepository, run_id: str) -> None:
    repository.start_sync_run(
        run_id=run_id,
        source="eodhd_fundamentals",
        started_at_utc=CALCULATED,
        requested_start_date=None,
        requested_end_date=None,
        instrument_count=1,
    )


def test_fundamental_daily_upsert_retains_recomputable_inputs(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path}/radar.db"
    repository = MarketRadarRepository(database_url)
    repository.create_schema()
    assert repository.latest_fundamental_snapshot() is None
    for run_id, item in (("first", observation()), ("second", changed(forward_pe=21.0))):
        _start_fundamental(repository, run_id)
        repository.publish_fundamental_bundle_and_complete(
            run_id,
            observations=(item,),
            snapshot=snapshot(item),
        )
    assert repository.latest_fundamental_snapshot() == snapshot(changed(forward_pe=21.0))
    engine = create_engine(database_url)
    with Session(engine) as session:
        rows = session.scalars(select(FundamentalObservationRecord)).all()
        assert len(rows) == 1
        assert rows[0].run_id == "second"
        assert rows[0].available_at_utc is None
        stored = FundamentalObservation.model_validate_json(json.dumps(rows[0].payload_json))
        assert snapshot(stored) == repository.latest_fundamental_snapshot()
        assert len(session.scalars(select(FundamentalSnapshotRecord)).all()) == 1
        assert len(session.scalars(select(MarketRadarSyncRunRecord)).all()) == 2
    engine.dispose()
    repository.close()
    reader = MarketRadarRepository(database_url, read_only=True)
    assert reader.latest_fundamental_snapshot() == snapshot(stored)
    reader.close()


def test_fundamental_next_day_appends_and_failed_run_preserves_latest(tmp_path: Path) -> None:
    repository = MarketRadarRepository(f"sqlite:///{tmp_path}/radar.db")
    repository.create_schema()
    _start_fundamental(repository, "old")
    repository.publish_fundamental_bundle_and_complete(
        "old", observations=(observation(),), snapshot=snapshot()
    )
    tomorrow = CALCULATED + timedelta(days=1)
    item = changed(captured_on=tomorrow.date())
    next_snapshot = calculate_fundamental_snapshot(
        as_of_date=tomorrow.date(), calculated_at_utc=tomorrow, observations=(item,)
    )
    repository.start_sync_run(
        run_id="new",
        source="eodhd_fundamentals",
        started_at_utc=tomorrow,
        requested_start_date=None,
        requested_end_date=None,
        instrument_count=1,
    )
    repository.publish_fundamental_bundle_and_complete(
        "new", observations=(item,), snapshot=next_snapshot
    )
    _start_fundamental(repository, "failed")
    repository.fail_sync_run("failed", completed_at_utc=tomorrow, error_summary="ValueError")
    assert repository.latest_fundamental_snapshot() == next_snapshot
    engine = create_engine(f"sqlite:///{tmp_path}/radar.db")
    with Session(engine) as session:
        assert len(session.scalars(select(FundamentalSnapshotRecord)).all()) == 2
    engine.dispose()
    repository.close()


def test_fundamental_transaction_failure_rolls_back_inputs_snapshot_and_status(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite:///{tmp_path}/radar.db"
    repository = MarketRadarRepository(database_url)
    repository.create_schema()
    _start_fundamental(repository, "old")
    repository.publish_fundamental_bundle_and_complete(
        "old", observations=(observation(),), snapshot=snapshot()
    )
    _start_fundamental(repository, "new")

    def fail_commit(session: Session) -> None:
        session.flush()
        raise RuntimeError("simulated commit failure")

    event.listen(Session, "before_commit", fail_commit)
    try:
        with pytest.raises(RuntimeError, match="simulated"):
            repository.publish_fundamental_bundle_and_complete(
                "new",
                observations=(changed(forward_pe=99.0),),
                snapshot=snapshot(changed(forward_pe=99.0)),
            )
    finally:
        event.remove(Session, "before_commit", fail_commit)
    assert repository.latest_fundamental_snapshot() == snapshot()
    run = repository.get_sync_run("new")
    assert run is not None
    assert run.status == "RUNNING"
    engine = create_engine(database_url)
    with Session(engine) as session:
        row = session.scalar(select(FundamentalObservationRecord))
        assert row is not None
        assert row.run_id == "old"
        assert row.payload_json["forward_pe"] == 20
    engine.dispose()
    repository.close()


@pytest.mark.parametrize(
    "case",
    [
        "no_run",
        "complete",
        "inputs",
        "source",
        "count",
        "date",
        "history",
        "backwards",
        "older_batch",
    ],
)
def test_fundamental_publish_rejects_inconsistent_batches(tmp_path: Path, case: str) -> None:
    database_url = f"sqlite:///{tmp_path}/radar.db"
    repository = MarketRadarRepository(database_url)
    repository.create_schema()
    _start_fundamental(repository, "run")
    if case == "older_batch":
        result = calculate_fundamental_snapshot(
            as_of_date=CALCULATED.date(),
            calculated_at_utc=CALCULATED + timedelta(hours=1),
            observations=(observation(),),
        )
        _start_fundamental(repository, "newer")
        repository.publish_fundamental_bundle_and_complete(
            "newer", observations=(observation(),), snapshot=result
        )
    engine = create_engine(database_url)
    with Session(engine) as session, session.begin():
        run = session.get(MarketRadarSyncRunRecord, "run")
        assert run is not None
        if case == "complete":
            run.status = "COMPLETE"
        elif case == "source":
            run.source = "eodhd_prices"
        elif case == "count":
            run.instrument_count = 2
        elif case == "date":
            run.started_at_utc = CALCULATED - timedelta(days=1)
        elif case == "backwards":
            run.started_at_utc = CALCULATED + timedelta(minutes=1)
        elif case == "history":
            run.requested_start_date = date(2020, 1, 1)
    engine.dispose()
    with pytest.raises((ValueError, LookupError)):
        repository.publish_fundamental_bundle_and_complete(
            "absent" if case == "no_run" else "run",
            observations=(changed(forward_pe=99.0) if case == "inputs" else observation(),),
            snapshot=snapshot(),
        )
    repository.close()


@pytest.mark.parametrize("case", ["payload", "metadata", "incomplete"])
def test_fundamental_reader_checks_published_payload_and_metadata(
    tmp_path: Path, case: str
) -> None:
    database_url = f"sqlite:///{tmp_path}/radar.db"
    repository = MarketRadarRepository(database_url)
    repository.create_schema()
    _start_fundamental(repository, "run")
    repository.publish_fundamental_bundle_and_complete(
        "run", observations=(observation(),), snapshot=snapshot()
    )
    engine = create_engine(database_url)
    with Session(engine) as session, session.begin():
        record = session.scalar(select(FundamentalSnapshotRecord))
        assert record is not None
        if case == "payload":
            record.payload_json = {"validity": "complete"}
        elif case == "metadata":
            record.calculated_at_utc = CALCULATED + timedelta(minutes=1)
        else:
            run = session.get(MarketRadarSyncRunRecord, "run")
            assert run is not None
            run.status = "RUNNING"
    engine.dispose()
    if case == "incomplete":
        assert repository.latest_fundamental_snapshot() is None
    else:
        with pytest.raises(RuntimeError, match=r"invalid|inconsistent"):
            repository.latest_fundamental_snapshot()
    repository.close()


def test_fundamental_schema_adds_tables_to_existing_market_database(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path}/radar.db"
    repository = MarketRadarRepository(database_url)
    repository.create_schema()
    _start(repository, "legacy")
    engine = create_engine(database_url)
    MarketRadarBase.metadata.tables["fundamental_observations"].drop(engine)
    MarketRadarBase.metadata.tables["fundamental_snapshots"].drop(engine)
    repository.create_schema()
    repository.create_schema()
    assert "fundamental_observations" in inspect(engine).get_table_names()
    assert "fundamental_snapshots" in inspect(engine).get_table_names()
    assert repository.get_sync_run("legacy") is not None
    engine.dispose()
    repository.close()


def test_fundamental_read_on_old_schema_does_not_migrate(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path}/radar.db"
    writer = MarketRadarRepository(database_url)
    writer.create_schema()
    writer.close()
    engine = create_engine(database_url)
    MarketRadarBase.metadata.tables["fundamental_observations"].drop(engine)
    MarketRadarBase.metadata.tables["fundamental_snapshots"].drop(engine)
    before = inspect(engine).get_table_names()
    reader = MarketRadarRepository(database_url, read_only=True)
    try:
        assert reader.latest_fundamental_snapshot() is None
        assert inspect(engine).get_table_names() == before
    finally:
        reader.close()
        engine.dispose()


def _start_economic(
    repository: MarketRadarRepository,
    run_id: str,
    started_at: datetime = ECONOMIC_AT,
) -> None:
    repository.start_sync_run(
        run_id=run_id,
        source="eodhd_economic_events",
        started_at_utc=started_at,
        requested_start_date=started_at.date(),
        requested_end_date=started_at.date() + timedelta(days=30),
        instrument_count=0,
    )


@pytest.mark.parametrize(
    ("source", "count", "allowed"),
    [
        ("eodhd_economic_events", 0, True),
        ("eodhd_economic_events", 1, False),
        ("eodhd_economic_events", -1, False),
        ("eodhd_prices", 0, False),
        ("eodhd_prices", -1, False),
        ("eodhd_prices", 1, True),
    ],
)
def test_zero_instrument_count_is_exclusive_to_economic_events(
    tmp_path: Path,
    source: str,
    count: int,
    allowed: bool,
) -> None:
    repository = MarketRadarRepository(f"sqlite:///{tmp_path}/radar.db")
    repository.create_schema()
    try:
        if allowed:
            repository.start_sync_run(
                run_id="run",
                source=source,
                started_at_utc=ECONOMIC_AT,
                requested_start_date=ECONOMIC_AT.date(),
                requested_end_date=ECONOMIC_AT.date() + timedelta(days=30),
                instrument_count=count,
            )
            assert repository.get_sync_run("run") is not None
        else:
            with pytest.raises(ValueError, match="instrument"):
                repository.start_sync_run(
                    run_id="run",
                    source=source,
                    started_at_utc=ECONOMIC_AT,
                    requested_start_date=ECONOMIC_AT.date(),
                    requested_end_date=ECONOMIC_AT.date() + timedelta(days=30),
                    instrument_count=count,
                )
            assert repository.get_sync_run("run") is None
    finally:
        repository.close()


def test_economic_same_day_replaces_reschedules_withdrawals_and_empty_batch(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path}/radar.db"
    repository = MarketRadarRepository(database_url)
    repository.create_schema()
    _start(repository, "price")
    price = _snapshot()
    repository.publish_price_snapshot_and_complete(
        "price",
        snapshot=price,
        completed_at_utc=STARTED,
        instruments_processed=2,
        bars_fetched=0,
        bars_written=0,
    )
    _start_economic(repository, "old")
    repository.publish_economic_events_and_complete("old", snapshot=economic_snapshot())
    moved = economic_event(event_date=ECONOMIC_AT.date() + timedelta(days=1), source_time=None)
    replacement = economic_snapshot(
        captured_at_utc=ECONOMIC_AT + timedelta(minutes=1),
        batch=EconomicEventBatch(
            events=(moved,),
            request_count=1,
            raw_record_count=1,
            duplicate_count=0,
        ),
    )
    _start_economic(repository, "rescheduled")
    repository.publish_economic_events_and_complete("rescheduled", snapshot=replacement)
    assert repository.latest_economic_event_snapshot() == replacement
    assert repository.latest_economic_event_snapshot() != economic_snapshot()
    empty = economic_snapshot(
        captured_at_utc=ECONOMIC_AT + timedelta(minutes=2),
        batch=EconomicEventBatch(events=(), request_count=1, raw_record_count=0, duplicate_count=0),
    )
    _start_economic(repository, "empty")
    repository.publish_economic_events_and_complete("empty", snapshot=empty)
    assert repository.latest_economic_event_snapshot() == empty
    assert repository.latest_price_snapshot() == price
    engine = create_engine(database_url)
    with Session(engine) as session:
        records = session.scalars(select(EconomicEventSnapshotRecord)).all()
        assert len(records) == 1
        assert records[0].run_id == "empty"
    engine.dispose()
    for run_id in ("old", "rescheduled", "empty"):
        run = repository.get_sync_run(run_id)
        assert run is not None
        assert run.status == "COMPLETE"
        assert run.instrument_count == run.instruments_processed == run.bars_fetched == 0
        assert run.bars_written == 0
    repository.close()
    path = tmp_path / "radar.db"
    before = path.read_bytes()
    reader = MarketRadarRepository(database_url, read_only=True)
    assert reader.latest_economic_event_snapshot() == empty
    reader.close()
    assert path.read_bytes() == before


def test_economic_next_date_appends_and_failed_run_keeps_latest(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path}/radar.db"
    repository = MarketRadarRepository(database_url)
    repository.create_schema()
    _start_economic(repository, "old")
    repository.publish_economic_events_and_complete("old", snapshot=economic_snapshot())
    tomorrow = ECONOMIC_AT + timedelta(days=1)
    newer = economic_snapshot(
        as_of_date=tomorrow.date(),
        captured_at_utc=tomorrow,
        window_start=tomorrow.date(),
        window_end=tomorrow.date() + timedelta(days=30),
        batch=EconomicEventBatch(events=(), request_count=1, raw_record_count=0, duplicate_count=0),
    )
    _start_economic(repository, "new", tomorrow)
    repository.publish_economic_events_and_complete("new", snapshot=newer)
    _start_economic(repository, "failed", tomorrow)
    repository.fail_sync_run("failed", completed_at_utc=tomorrow, error_summary="ValueError")
    assert repository.latest_economic_event_snapshot() == newer
    engine = create_engine(database_url)
    with Session(engine) as session:
        assert len(session.scalars(select(EconomicEventSnapshotRecord)).all()) == 2
    engine.dispose()
    repository.close()


def test_economic_commit_failure_rolls_back_snapshot_and_run(tmp_path: Path) -> None:
    repository = MarketRadarRepository(f"sqlite:///{tmp_path}/radar.db")
    repository.create_schema()
    _start_economic(repository, "old")
    repository.publish_economic_events_and_complete("old", snapshot=economic_snapshot())
    _start_economic(repository, "new")

    def fail_commit(session: Session) -> None:
        session.flush()
        raise RuntimeError("simulated economic commit failure")

    event.listen(Session, "before_commit", fail_commit)
    try:
        with pytest.raises(RuntimeError, match="simulated economic"):
            repository.publish_economic_events_and_complete(
                "new",
                snapshot=economic_snapshot(captured_at_utc=ECONOMIC_AT + timedelta(minutes=1)),
            )
    finally:
        event.remove(Session, "before_commit", fail_commit)
    assert repository.latest_economic_event_snapshot() == economic_snapshot()
    run = repository.get_sync_run("new")
    assert run is not None
    assert run.status == "RUNNING"
    assert run.completed_at_utc is None
    repository.close()


@pytest.mark.parametrize(
    "case",
    [
        "no_run",
        "complete",
        "source",
        "count",
        "processed",
        "fetched",
        "written",
        "date",
        "backwards",
        "range_start",
        "range_end",
        "completed_at",
        "error",
        "older_batch",
        "copied_payload",
    ],
)
def test_economic_publication_rejects_inconsistent_run_or_stale_replacement(
    tmp_path: Path,
    case: str,
) -> None:
    database_url = f"sqlite:///{tmp_path}/radar.db"
    repository = MarketRadarRepository(database_url)
    repository.create_schema()
    _start_economic(repository, "run")
    target = economic_snapshot()
    if case == "older_batch":
        _start_economic(repository, "newer")
        repository.publish_economic_events_and_complete(
            "newer",
            snapshot=economic_snapshot(captured_at_utc=ECONOMIC_AT + timedelta(minutes=1)),
        )
    if case == "copied_payload":
        target = target.model_copy(update={"source": "bad-source"})
    engine = create_engine(database_url)
    with Session(engine) as session, session.begin():
        run = session.get(MarketRadarSyncRunRecord, "run")
        assert run is not None
        changes: dict[str, object] = {
            "complete": "COMPLETE",
            "source": "eodhd_prices",
            "count": 1,
            "processed": 1,
            "fetched": 1,
            "written": 1,
            "date": ECONOMIC_AT - timedelta(days=1),
            "backwards": ECONOMIC_AT + timedelta(minutes=1),
            "range_start": None,
            "range_end": None,
            "completed_at": ECONOMIC_AT,
            "error": "stale error",
        }
        fields = {
            "complete": "status",
            "source": "source",
            "count": "instrument_count",
            "processed": "instruments_processed",
            "fetched": "bars_fetched",
            "written": "bars_written",
            "date": "started_at_utc",
            "backwards": "started_at_utc",
            "range_start": "requested_start_date",
            "range_end": "requested_end_date",
            "completed_at": "completed_at_utc",
            "error": "error_summary",
        }
        if case in fields:
            setattr(run, fields[case], changes[case])
    engine.dispose()
    with pytest.raises((ValueError, LookupError), match=r"capture batch|RUNNING|newer|validation"):
        repository.publish_economic_events_and_complete(
            "absent" if case == "no_run" else "run",
            snapshot=target,
        )
    if case == "older_batch":
        assert repository.latest_economic_event_snapshot() == economic_snapshot(
            captured_at_utc=ECONOMIC_AT + timedelta(minutes=1),
        )
        unchanged_run = repository.get_sync_run("run")
        assert unchanged_run is not None
        assert unchanged_run.status == "RUNNING"
    repository.close()


@pytest.mark.parametrize(
    "case",
    [
        "payload",
        "metadata",
        "source",
        "date",
        "backwards",
        "count",
        "processed",
        "range",
        "completed_at",
        "error",
        "orphan",
        "incomplete",
        "bad_json",
        "bad_time",
    ],
)
def test_economic_reader_checks_payload_and_complete_run_metadata(
    tmp_path: Path,
    case: str,
) -> None:
    database_url = f"sqlite:///{tmp_path}/radar.db"
    writer = MarketRadarRepository(database_url)
    writer.create_schema()
    _start_economic(writer, "run")
    writer.publish_economic_events_and_complete("run", snapshot=economic_snapshot())
    writer.close()
    engine = create_engine(database_url)
    with Session(engine) as session, session.begin():
        row = session.scalar(select(EconomicEventSnapshotRecord))
        run = session.get(MarketRadarSyncRunRecord, "run")
        assert row is not None
        assert run is not None
        if case == "payload":
            row.payload_json = {"batch": []}
        elif case == "metadata":
            row.captured_at_utc = ECONOMIC_AT + timedelta(minutes=1)
        elif case == "source":
            run.source = "eodhd_prices"
        elif case == "date":
            run.started_at_utc = ECONOMIC_AT - timedelta(days=1)
        elif case == "backwards":
            run.started_at_utc = ECONOMIC_AT + timedelta(minutes=1)
        elif case == "count":
            run.instrument_count = 1
        elif case == "processed":
            run.instruments_processed = 1
        elif case == "range":
            run.requested_end_date = None
        elif case == "completed_at":
            run.completed_at_utc = None
        elif case == "error":
            run.error_summary = "error"
        elif case == "orphan":
            row.run_id = "absent"
        elif case == "incomplete":
            run.status = "RUNNING"
    with engine.begin() as connection:
        if case == "bad_json":
            connection.exec_driver_sql("UPDATE economic_event_snapshots SET payload_json='broken'")
        elif case == "bad_time":
            connection.exec_driver_sql("UPDATE economic_event_snapshots SET captured_at_utc='bad'")
    engine.dispose()
    reader = MarketRadarRepository(database_url, read_only=True)
    try:
        if case == "incomplete":
            assert reader.latest_economic_event_snapshot() is None
        else:
            with pytest.raises(RuntimeError, match=r"invalid|inconsistent|no run"):
                reader.latest_economic_event_snapshot()
    finally:
        reader.close()


@pytest.mark.parametrize("case", ["old_schema", "empty", "broken_table", "missing_run_table"])
def test_economic_reader_never_creates_schema_or_hides_broken_tables(
    tmp_path: Path,
    case: str,
) -> None:
    database_url = f"sqlite:///{tmp_path}/radar.db"
    writer = MarketRadarRepository(database_url)
    writer.create_schema()
    _start(writer, "legacy")
    writer.close()
    engine = create_engine(database_url)
    if case in {"old_schema", "broken_table"}:
        MarketRadarBase.metadata.tables["economic_event_snapshots"].drop(engine)
    if case == "broken_table":
        with engine.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE economic_event_snapshots (bad TEXT)")
    elif case == "missing_run_table":
        MarketRadarBase.metadata.tables["sync_runs"].drop(engine)
    before_tables = inspect(engine).get_table_names()
    before = (tmp_path / "radar.db").read_bytes()
    reader = MarketRadarRepository(database_url, read_only=True)
    try:
        if case in {"broken_table", "missing_run_table"}:
            with pytest.raises(OperationalError):
                reader.latest_economic_event_snapshot()
        else:
            assert reader.latest_economic_event_snapshot() is None
        assert inspect(engine).get_table_names() == before_tables
        assert (tmp_path / "radar.db").read_bytes() == before
    finally:
        reader.close()
        engine.dispose()
    if case == "old_schema":
        writer = MarketRadarRepository(database_url)
        writer.create_schema()
        writer.create_schema()
        assert writer.get_sync_run("legacy") is not None
        assert writer.latest_economic_event_snapshot() is None
        writer.close()

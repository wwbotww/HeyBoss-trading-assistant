"""市场雷达独立 SQLite 仓储。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal, cast

from sqlalchemy import create_engine, delete, select, update
from sqlalchemy.engine import CursorResult, Engine
from sqlalchemy.orm import Session, sessionmaker

from trading_assistant.market_radar.earnings import (
    EarningsCalendarEvent,
    EarningsRevisionSnapshot,
    Fy1EarningsTrend,
)
from trading_assistant.market_radar.fred import FredObservation
from trading_assistant.market_radar.macro import RiskAppetiteSnapshot
from trading_assistant.market_radar.membership import (
    CurrentMarketMember,
    CurrentMarketMembership,
    CurrentMarketSectorClassification,
)
from trading_assistant.market_radar.metrics import CurrentBreadthSnapshot, PriceRadarSnapshot
from trading_assistant.market_radar.models import (
    CurrentBreadthSnapshotRecord,
    CurrentMarketMemberRecord,
    EarningsCalendarEventRecord,
    EarningsMarketMemberRecord,
    EarningsRevisionSnapshotRecord,
    EarningsTrendObservationRecord,
    MacroObservationRecord,
    MacroRegimeSnapshotRecord,
    MarketRadarBase,
    MarketRadarSyncRunRecord,
    PriceSnapshotRecord,
    RiskAppetiteSnapshotRecord,
)
from trading_assistant.market_radar.regime import MacroRegimeSnapshot

SyncRunStatus = Literal["RUNNING", "COMPLETE", "FAILED"]
_SYNC_RUN_STATUSES = frozenset({"RUNNING", "COMPLETE", "FAILED"})


@dataclass(frozen=True)
class MarketRadarSyncRun:
    """脱离 SQLAlchemy Session 的同步运行快照。"""

    run_id: str
    source: str
    status: SyncRunStatus
    started_at_utc: datetime
    completed_at_utc: datetime | None
    requested_start_date: date | None
    requested_end_date: date | None
    instrument_count: int
    instruments_processed: int
    bars_fetched: int
    bars_written: int
    error_summary: str | None


def _utc(value: datetime) -> datetime:
    """要求写入感知时区的时间, 并规范成 UTC。"""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("market radar timestamps must be timezone-aware")
    return value.astimezone(UTC)


def _loaded_utc(value: datetime | None) -> datetime | None:
    """恢复 SQLite 丢失的 UTC tzinfo。"""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class MarketRadarRepository:
    """管理独立的市场雷达同步运行状态。"""

    def __init__(self, database_url: str, *, read_only: bool = False) -> None:
        prefix = "sqlite:///"
        if not database_url.startswith(prefix):
            raise ValueError("Market radar repository requires a SQLite database")
        if not read_only:
            self._ensure_parent(database_url)
        effective_url = self._read_only_url(database_url) if read_only else database_url
        self._engine: Engine = create_engine(effective_url, connect_args={"timeout": 30})
        self._sessions = sessionmaker(self._engine, expire_on_commit=False)

    @staticmethod
    def _read_only_url(database_url: str) -> str:
        database_path = database_url.removeprefix("sqlite:///")
        if database_path == ":memory:":
            raise ValueError("Read-only repository cannot use an in-memory database")
        absolute_path = Path(database_path).expanduser().resolve()
        return f"sqlite:///file:{absolute_path}?mode=ro&uri=true"

    @staticmethod
    def _ensure_parent(database_url: str) -> None:
        database_path = database_url.removeprefix("sqlite:///")
        if database_path != ":memory:":
            Path(database_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)

    def create_schema(self) -> None:
        """幂等创建市场雷达表。"""
        MarketRadarBase.metadata.create_all(self._engine)

    def close(self) -> None:
        """释放数据库连接池。"""
        self._engine.dispose()

    def healthcheck(self) -> None:
        """执行不依赖业务表的只读连接检查。"""
        with Session(self._engine) as session:
            session.scalar(select(1))

    def start_sync_run(
        self,
        *,
        run_id: str,
        source: str,
        started_at_utc: datetime,
        requested_start_date: date | None,
        requested_end_date: date | None,
        instrument_count: int,
    ) -> None:
        """创建 RUNNING 记录。"""
        if not run_id or len(run_id) > 64:
            raise ValueError("market radar run_id must contain 1 to 64 characters")
        if not source or len(source) > 32:
            raise ValueError("market radar source must contain 1 to 32 characters")
        if instrument_count < 1:
            raise ValueError("market radar sync requires at least one instrument")
        if (
            requested_start_date is not None
            and requested_end_date is not None
            and requested_start_date >= requested_end_date
        ):
            raise ValueError("market radar start date must be earlier than end date")
        with self._sessions.begin() as session:
            session.add(
                MarketRadarSyncRunRecord(
                    run_id=run_id,
                    source=source,
                    status="RUNNING",
                    started_at_utc=_utc(started_at_utc),
                    completed_at_utc=None,
                    requested_start_date=requested_start_date,
                    requested_end_date=requested_end_date,
                    instrument_count=instrument_count,
                    instruments_processed=0,
                    bars_fetched=0,
                    bars_written=0,
                    error_summary=None,
                )
            )

    def publish_price_snapshot_and_complete(
        self,
        run_id: str,
        *,
        snapshot: PriceRadarSnapshot,
        completed_at_utc: datetime,
        instruments_processed: int,
        bars_fetched: int,
        bars_written: int,
    ) -> None:
        """在一个事务内发布快照并把对应 RUNNING 转成 COMPLETE。"""
        if min(instruments_processed, bars_fetched, bars_written) < 0:
            raise ValueError("market radar sync counts cannot be negative")
        completed_at = _utc(completed_at_utc)
        with self._sessions.begin() as session:
            run = session.get(MarketRadarSyncRunRecord, run_id)
            if run is None or run.status != "RUNNING":
                raise LookupError(f"RUNNING market radar sync run not found: {run_id}")
            existing = session.get(PriceSnapshotRecord, snapshot.as_of_date)
            payload = snapshot.to_payload()
            calculated_at = _utc(snapshot.calculated_at_utc)
            if existing is None:
                session.add(
                    PriceSnapshotRecord(
                        as_of_date=snapshot.as_of_date,
                        run_id=run_id,
                        calculated_at_utc=calculated_at,
                        payload_json=payload,
                    )
                )
            else:
                existing.run_id = run_id
                existing.calculated_at_utc = calculated_at
                existing.payload_json = payload
            run.status = "COMPLETE"
            run.completed_at_utc = completed_at
            run.instruments_processed = instruments_processed
            run.bars_fetched = bars_fetched
            run.bars_written = bars_written
            run.error_summary = None

    def latest_price_snapshot(self) -> PriceRadarSnapshot | None:
        """返回最近 COMPLETE 运行发布的价格快照。"""
        with self._sessions() as session:
            record = session.scalar(
                select(PriceSnapshotRecord)
                .join(
                    MarketRadarSyncRunRecord,
                    MarketRadarSyncRunRecord.run_id == PriceSnapshotRecord.run_id,
                )
                .where(MarketRadarSyncRunRecord.status == "COMPLETE")
                .order_by(
                    PriceSnapshotRecord.as_of_date.desc(),
                    PriceSnapshotRecord.calculated_at_utc.desc(),
                )
                .limit(1)
            )
            if record is None:
                return None
            try:
                snapshot = PriceRadarSnapshot.from_payload(record.payload_json)
            except (TypeError, ValueError) as exc:
                raise RuntimeError("persisted market radar price snapshot is invalid") from exc
            calculated_at = _loaded_utc(record.calculated_at_utc)
            if (
                snapshot.as_of_date != record.as_of_date
                or calculated_at is None
                or snapshot.calculated_at_utc != calculated_at
            ):
                raise RuntimeError("persisted market radar price snapshot metadata is inconsistent")
            return snapshot

    def publish_current_breadth_and_complete(
        self,
        run_id: str,
        *,
        membership: CurrentMarketMembership,
        snapshot: CurrentBreadthSnapshot,
        completed_at_utc: datetime,
        instruments_processed: int,
        bars_fetched: int,
        bars_written: int,
    ) -> None:
        """在同一事务内发布成员、宽度快照和 COMPLETE 状态。"""
        if min(instruments_processed, bars_fetched, bars_written) < 0:
            raise ValueError("market radar sync counts cannot be negative")
        if (
            snapshot.membership_date != membership.membership_date
            or snapshot.membership_source != membership.source
            or snapshot.member_count != len(membership.members)
        ):
            raise ValueError("breadth snapshot does not match its market membership")
        completed_at = _utc(completed_at_utc)
        with self._sessions.begin() as session:
            run = session.get(MarketRadarSyncRunRecord, run_id)
            if run is None or run.status != "RUNNING":
                raise LookupError(f"RUNNING market radar sync run not found: {run_id}")
            if run.instrument_count != len(membership.members):
                raise ValueError("breadth run instrument count does not match membership")

            session.execute(
                delete(CurrentMarketMemberRecord).where(
                    CurrentMarketMemberRecord.membership_date == membership.membership_date
                )
            )
            session.add_all(
                CurrentMarketMemberRecord(
                    membership_date=membership.membership_date,
                    instrument_id=member.instrument_id,
                    run_id=run_id,
                    source=membership.source,
                    source_symbol=member.source_symbol,
                    data_symbol=member.data_symbol,
                )
                for member in membership.members
            )

            existing = session.get(CurrentBreadthSnapshotRecord, snapshot.as_of_date)
            payload = snapshot.to_payload()
            calculated_at = _utc(snapshot.calculated_at_utc)
            if existing is None:
                session.add(
                    CurrentBreadthSnapshotRecord(
                        as_of_date=snapshot.as_of_date,
                        run_id=run_id,
                        membership_date=snapshot.membership_date,
                        calculated_at_utc=calculated_at,
                        payload_json=payload,
                    )
                )
            else:
                existing.run_id = run_id
                existing.membership_date = snapshot.membership_date
                existing.calculated_at_utc = calculated_at
                existing.payload_json = payload

            run.status = "COMPLETE"
            run.completed_at_utc = completed_at
            run.instruments_processed = instruments_processed
            run.bars_fetched = bars_fetched
            run.bars_written = bars_written
            run.error_summary = None

    def latest_current_breadth_snapshot(self) -> CurrentBreadthSnapshot | None:
        """返回最近 COMPLETE 运行发布的当前宽度快照。"""
        with self._sessions() as session:
            record = session.scalar(
                select(CurrentBreadthSnapshotRecord)
                .join(
                    MarketRadarSyncRunRecord,
                    MarketRadarSyncRunRecord.run_id == CurrentBreadthSnapshotRecord.run_id,
                )
                .where(MarketRadarSyncRunRecord.status == "COMPLETE")
                .order_by(
                    CurrentBreadthSnapshotRecord.as_of_date.desc(),
                    CurrentBreadthSnapshotRecord.calculated_at_utc.desc(),
                )
                .limit(1)
            )
            if record is None:
                return None
            try:
                snapshot = CurrentBreadthSnapshot.from_payload(record.payload_json)
            except (TypeError, ValueError) as exc:
                raise RuntimeError("persisted current breadth snapshot is invalid") from exc
            calculated_at = _loaded_utc(record.calculated_at_utc)
            if (
                snapshot.as_of_date != record.as_of_date
                or snapshot.membership_date != record.membership_date
                or calculated_at is None
                or snapshot.calculated_at_utc != calculated_at
            ):
                raise RuntimeError("persisted current breadth snapshot metadata is inconsistent")
            return snapshot

    def publish_macro_bundle_and_complete(
        self,
        run_id: str,
        *,
        risk_snapshot: RiskAppetiteSnapshot,
        observations: tuple[FredObservation, ...],
        regime_snapshot: MacroRegimeSnapshot,
        ingested_at_utc: datetime,
        completed_at_utc: datetime,
        instruments_processed: int,
        bars_fetched: int,
        bars_written: int,
    ) -> None:
        """在一个事务内发布两类宏观快照、当前修订观测和 COMPLETE。"""
        if min(instruments_processed, bars_fetched, bars_written) < 0:
            raise ValueError("market radar sync counts cannot be negative")
        if not observations:
            raise ValueError("macro publication requires real-rate observations")
        if (
            regime_snapshot.as_of_date != risk_snapshot.as_of_date
            or regime_snapshot.risk_appetite_as_of_date != risk_snapshot.as_of_date
            or regime_snapshot.credit_source != risk_snapshot.credit_source
            or regime_snapshot.price_source != risk_snapshot.price_source
        ):
            raise ValueError("macro regime snapshot does not match risk appetite snapshot")
        series_ids = {item.series_id for item in observations}
        if series_ids != {regime_snapshot.real_rate.series_id}:
            raise ValueError("macro observations do not match the regime real-rate series")
        observation_days = tuple(item.observation_date for item in observations)
        if observation_days != tuple(sorted(set(observation_days))):
            raise ValueError("macro observations must be unique and increasing")
        eligible = tuple(
            item for item in observations if item.observation_date <= regime_snapshot.as_of_date
        )
        if not eligible or (
            eligible[-1].observation_date != regime_snapshot.real_rate.latest_observation_date
            or eligible[-1].value != regime_snapshot.real_rate.level_percent
        ):
            raise ValueError("macro observations do not match the latest real-rate state")
        completed_at = _utc(completed_at_utc)
        ingested_at = _utc(ingested_at_utc)
        with self._sessions.begin() as session:
            run = session.get(MarketRadarSyncRunRecord, run_id)
            if run is None or run.status != "RUNNING":
                raise LookupError(f"RUNNING market radar sync run not found: {run_id}")

            for observation in observations:
                key = ("fred", observation.series_id, observation.observation_date)
                existing_observation = session.get(MacroObservationRecord, key)
                if existing_observation is None:
                    session.add(
                        MacroObservationRecord(
                            source="fred",
                            series_id=observation.series_id,
                            observation_date=observation.observation_date,
                            run_id=run_id,
                            value=observation.value,
                            realtime_start=observation.realtime_start,
                            realtime_end=observation.realtime_end,
                            ingested_at_utc=ingested_at,
                        )
                    )
                else:
                    existing_observation.run_id = run_id
                    existing_observation.value = observation.value
                    existing_observation.realtime_start = observation.realtime_start
                    existing_observation.realtime_end = observation.realtime_end
                    existing_observation.ingested_at_utc = ingested_at

            existing_risk = session.get(
                RiskAppetiteSnapshotRecord,
                risk_snapshot.as_of_date,
            )
            risk_payload = risk_snapshot.to_payload()
            risk_calculated_at = _utc(risk_snapshot.calculated_at_utc)
            if existing_risk is None:
                session.add(
                    RiskAppetiteSnapshotRecord(
                        as_of_date=risk_snapshot.as_of_date,
                        run_id=run_id,
                        calculated_at_utc=risk_calculated_at,
                        payload_json=risk_payload,
                    )
                )
            else:
                existing_risk.run_id = run_id
                existing_risk.calculated_at_utc = risk_calculated_at
                existing_risk.payload_json = risk_payload

            existing_regime = session.get(
                MacroRegimeSnapshotRecord,
                regime_snapshot.as_of_date,
            )
            regime_payload = regime_snapshot.to_payload()
            regime_calculated_at = _utc(regime_snapshot.calculated_at_utc)
            if existing_regime is None:
                session.add(
                    MacroRegimeSnapshotRecord(
                        as_of_date=regime_snapshot.as_of_date,
                        run_id=run_id,
                        calculated_at_utc=regime_calculated_at,
                        payload_json=regime_payload,
                    )
                )
            else:
                existing_regime.run_id = run_id
                existing_regime.calculated_at_utc = regime_calculated_at
                existing_regime.payload_json = regime_payload

            run.status = "COMPLETE"
            run.completed_at_utc = completed_at
            run.instruments_processed = instruments_processed
            run.bars_fetched = bars_fetched
            run.bars_written = bars_written
            run.error_summary = None

    def latest_risk_appetite_snapshot(self) -> RiskAppetiteSnapshot | None:
        """返回最近 COMPLETE 运行发布的风险偏好快照。"""
        with self._sessions() as session:
            record = session.scalar(
                select(RiskAppetiteSnapshotRecord)
                .join(
                    MarketRadarSyncRunRecord,
                    MarketRadarSyncRunRecord.run_id == RiskAppetiteSnapshotRecord.run_id,
                )
                .where(MarketRadarSyncRunRecord.status == "COMPLETE")
                .order_by(
                    RiskAppetiteSnapshotRecord.as_of_date.desc(),
                    RiskAppetiteSnapshotRecord.calculated_at_utc.desc(),
                )
                .limit(1)
            )
            if record is None:
                return None
            try:
                snapshot = RiskAppetiteSnapshot.from_payload(record.payload_json)
            except (TypeError, ValueError) as exc:
                raise RuntimeError("persisted risk appetite snapshot is invalid") from exc
            calculated_at = _loaded_utc(record.calculated_at_utc)
            if (
                snapshot.as_of_date != record.as_of_date
                or calculated_at is None
                or snapshot.calculated_at_utc != calculated_at
            ):
                raise RuntimeError("persisted risk appetite snapshot metadata is inconsistent")
            return snapshot

    def latest_macro_regime_snapshot(self) -> MacroRegimeSnapshot | None:
        """返回最近 COMPLETE 运行发布的宏观象限快照。"""
        bundle = self.latest_macro_bundle()
        return None if bundle is None else bundle[1]

    def latest_macro_bundle(
        self,
    ) -> tuple[RiskAppetiteSnapshot, MacroRegimeSnapshot] | None:
        """在一个只读事务中返回同次运行发布的两类宏观快照。"""
        with self._sessions() as session:
            row = session.execute(
                select(MacroRegimeSnapshotRecord, RiskAppetiteSnapshotRecord)
                .join(
                    MarketRadarSyncRunRecord,
                    MarketRadarSyncRunRecord.run_id == MacroRegimeSnapshotRecord.run_id,
                )
                .join(
                    RiskAppetiteSnapshotRecord,
                    RiskAppetiteSnapshotRecord.run_id == MacroRegimeSnapshotRecord.run_id,
                )
                .where(MarketRadarSyncRunRecord.status == "COMPLETE")
                .order_by(
                    MacroRegimeSnapshotRecord.as_of_date.desc(),
                    MacroRegimeSnapshotRecord.calculated_at_utc.desc(),
                )
                .limit(1)
            ).one_or_none()
            if row is None:
                return None
            regime_record, risk_record = row
            try:
                regime = MacroRegimeSnapshot.from_payload(regime_record.payload_json)
                risk = RiskAppetiteSnapshot.from_payload(risk_record.payload_json)
            except (TypeError, ValueError) as exc:
                raise RuntimeError("persisted macro snapshot bundle is invalid") from exc
            regime_calculated_at = _loaded_utc(regime_record.calculated_at_utc)
            risk_calculated_at = _loaded_utc(risk_record.calculated_at_utc)
            if (
                regime.as_of_date != regime_record.as_of_date
                or risk.as_of_date != risk_record.as_of_date
                or regime.as_of_date != risk.as_of_date
                or regime_calculated_at is None
                or risk_calculated_at is None
                or regime.calculated_at_utc != regime_calculated_at
                or risk.calculated_at_utc != risk_calculated_at
            ):
                raise RuntimeError("persisted macro snapshot bundle metadata is inconsistent")
            return risk, regime

    def latest_current_membership(self) -> CurrentMarketMembership | None:
        """返回最近成功发布的规范当前成员快照。"""
        with self._sessions() as session:
            membership_date = session.scalar(
                select(CurrentMarketMemberRecord.membership_date)
                .join(
                    MarketRadarSyncRunRecord,
                    MarketRadarSyncRunRecord.run_id == CurrentMarketMemberRecord.run_id,
                )
                .where(MarketRadarSyncRunRecord.status == "COMPLETE")
                .order_by(CurrentMarketMemberRecord.membership_date.desc())
                .limit(1)
            )
            if membership_date is None:
                return None
            records = session.scalars(
                select(CurrentMarketMemberRecord)
                .join(
                    MarketRadarSyncRunRecord,
                    MarketRadarSyncRunRecord.run_id == CurrentMarketMemberRecord.run_id,
                )
                .where(
                    CurrentMarketMemberRecord.membership_date == membership_date,
                    MarketRadarSyncRunRecord.status == "COMPLETE",
                )
                .order_by(CurrentMarketMemberRecord.instrument_id)
            ).all()
            sources = {record.source for record in records}
            if len(sources) != 1:
                raise RuntimeError("persisted current market membership is inconsistent")
            return CurrentMarketMembership(
                source=sources.pop(),
                membership_date=membership_date,
                members=tuple(
                    CurrentMarketMember(
                        source_symbol=record.source_symbol,
                        instrument_id=record.instrument_id,
                        data_symbol=record.data_symbol,
                    )
                    for record in records
                ),
            )

    def publish_earnings_bundle_and_complete(
        self,
        run_id: str,
        *,
        membership: CurrentMarketMembership,
        classification: CurrentMarketSectorClassification,
        trends: tuple[Fy1EarningsTrend, ...],
        events: tuple[EarningsCalendarEvent, ...],
        snapshot: EarningsRevisionSnapshot,
        ingested_at_utc: datetime,
        completed_at_utc: datetime,
        instruments_processed: int,
    ) -> None:
        """同事务替换当日成员、观测、快照并完成同步运行。"""
        ingested_at = _utc(ingested_at_utc)
        completed_at = _utc(completed_at_utc)
        if snapshot.as_of_date != ingested_at.date():
            raise ValueError("earnings snapshot date must match its UTC ingestion date")
        member_ids = tuple(item.instrument_id for item in membership.members)
        assignment_ids = tuple(item.instrument_id for item in classification.assignments)
        if classification.requested_member_count != len(member_ids) or not set(
            assignment_ids
        ).issubset(member_ids):
            raise ValueError("earnings classification does not match membership")
        coverage = snapshot.membership
        if (
            coverage.membership_source != membership.source
            or coverage.membership_date != membership.membership_date
            or coverage.member_count != len(member_ids)
            or coverage.classification_source != classification.source
            or coverage.classification_record_count != classification.source_record_count
            or coverage.classified_member_count != classification.classified_member_count
            or coverage.unclassified_member_count != classification.unclassified_member_count
            or coverage.unused_classification_count != classification.unused_source_record_count
        ):
            raise ValueError("earnings snapshot coverage does not match its sources")
        trend_ids = tuple(item.instrument_id for item in trends)
        if len(trend_ids) != len(set(trend_ids)):
            raise ValueError("earnings trends must contain unique instruments")
        event_keys = tuple(
            (item.instrument_id, item.report_date, item.fiscal_period_end) for item in events
        )
        if len(event_keys) != len(set(event_keys)):
            raise ValueError("earnings events must contain unique business keys")

        with self._sessions.begin() as session:
            run = session.get(MarketRadarSyncRunRecord, run_id)
            if run is None or run.status != "RUNNING":
                raise LookupError(f"RUNNING market radar sync run not found: {run_id}")
            if (
                run.instrument_count < len(member_ids)
                or len(trend_ids) > run.instrument_count
                or instruments_processed != run.instrument_count
            ):
                raise ValueError("earnings run counts do not match the requested universe")

            sector_by_instrument = {
                item.instrument_id: item.sector for item in classification.assignments
            }
            session.execute(
                delete(EarningsMarketMemberRecord).where(
                    EarningsMarketMemberRecord.as_of_date == snapshot.as_of_date
                )
            )
            session.add_all(
                EarningsMarketMemberRecord(
                    as_of_date=snapshot.as_of_date,
                    instrument_id=item.instrument_id,
                    run_id=run_id,
                    membership_date=membership.membership_date,
                    membership_source=membership.source,
                    source_symbol=item.source_symbol,
                    data_symbol=item.data_symbol,
                    sector=sector_by_instrument.get(item.instrument_id),
                    classification_source=classification.source,
                    ingested_at_utc=ingested_at,
                )
                for item in membership.members
            )

            session.execute(
                delete(EarningsTrendObservationRecord).where(
                    EarningsTrendObservationRecord.as_of_date == snapshot.as_of_date
                )
            )
            session.add_all(
                EarningsTrendObservationRecord(
                    as_of_date=snapshot.as_of_date,
                    instrument_id=item.instrument_id,
                    run_id=run_id,
                    fiscal_period_end=item.fiscal_period_end,
                    eps_current=item.eps_current,
                    eps_30_days_ago=item.eps_30_days_ago,
                    analyst_count=item.analyst_count,
                    revisions_up_30_days=item.revisions_up_30_days,
                    revisions_down_30_days=item.revisions_down_30_days,
                    available_at_utc=None,
                    ingested_at_utc=ingested_at,
                )
                for item in trends
            )

            session.execute(
                delete(EarningsCalendarEventRecord).where(
                    EarningsCalendarEventRecord.as_of_date == snapshot.as_of_date
                )
            )
            session.add_all(
                EarningsCalendarEventRecord(
                    as_of_date=snapshot.as_of_date,
                    instrument_id=item.instrument_id,
                    report_date=item.report_date,
                    fiscal_period_end=item.fiscal_period_end,
                    run_id=run_id,
                    session=item.session,
                    currency=item.currency,
                    actual_eps=item.actual_eps,
                    estimated_eps=item.estimated_eps,
                    available_at_utc=None,
                    ingested_at_utc=ingested_at,
                )
                for item in events
            )

            existing = session.get(EarningsRevisionSnapshotRecord, snapshot.as_of_date)
            payload = snapshot.to_payload()
            calculated_at = _utc(snapshot.calculated_at_utc)
            if existing is None:
                session.add(
                    EarningsRevisionSnapshotRecord(
                        as_of_date=snapshot.as_of_date,
                        run_id=run_id,
                        calculated_at_utc=calculated_at,
                        payload_json=payload,
                    )
                )
            else:
                existing.run_id = run_id
                existing.calculated_at_utc = calculated_at
                existing.payload_json = payload

            run.status = "COMPLETE"
            run.completed_at_utc = completed_at
            run.instruments_processed = instruments_processed
            run.bars_fetched = 0
            run.bars_written = 0
            run.error_summary = None

    def latest_earnings_revision_snapshot(self) -> EarningsRevisionSnapshot | None:
        """返回最近 COMPLETE 运行发布的统一盈利修正快照。"""
        with self._sessions() as session:
            record = session.scalar(
                select(EarningsRevisionSnapshotRecord)
                .join(
                    MarketRadarSyncRunRecord,
                    MarketRadarSyncRunRecord.run_id == EarningsRevisionSnapshotRecord.run_id,
                )
                .where(MarketRadarSyncRunRecord.status == "COMPLETE")
                .order_by(
                    EarningsRevisionSnapshotRecord.as_of_date.desc(),
                    EarningsRevisionSnapshotRecord.calculated_at_utc.desc(),
                )
                .limit(1)
            )
            if record is None:
                return None
            try:
                snapshot = EarningsRevisionSnapshot.from_payload(record.payload_json)
            except (TypeError, ValueError) as exc:
                raise RuntimeError("persisted earnings revision snapshot is invalid") from exc
            calculated_at = _loaded_utc(record.calculated_at_utc)
            if (
                snapshot.as_of_date != record.as_of_date
                or calculated_at is None
                or snapshot.calculated_at_utc != calculated_at
            ):
                raise RuntimeError("persisted earnings revision snapshot metadata is inconsistent")
            return snapshot

    def fail_sync_run(
        self,
        run_id: str,
        *,
        completed_at_utc: datetime,
        error_summary: str,
        instruments_processed: int = 0,
        bars_fetched: int = 0,
        bars_written: int = 0,
    ) -> None:
        """只允许把 RUNNING 原子转换为 FAILED。"""
        if not error_summary.strip():
            raise ValueError("market radar failure summary cannot be empty")
        if min(instruments_processed, bars_fetched, bars_written) < 0:
            raise ValueError("market radar sync counts cannot be negative")
        with self._sessions.begin() as session:
            result = cast(
                CursorResult[Any],
                session.execute(
                    update(MarketRadarSyncRunRecord)
                    .where(
                        MarketRadarSyncRunRecord.run_id == run_id,
                        MarketRadarSyncRunRecord.status == "RUNNING",
                    )
                    .values(
                        status="FAILED",
                        completed_at_utc=_utc(completed_at_utc),
                        instruments_processed=instruments_processed,
                        bars_fetched=bars_fetched,
                        bars_written=bars_written,
                        error_summary=error_summary.strip(),
                    )
                ),
            )
            if result.rowcount != 1:
                raise LookupError(f"RUNNING market radar sync run not found: {run_id}")

    def get_sync_run(self, run_id: str) -> MarketRadarSyncRun | None:
        """按 ID 返回同步运行。"""
        with self._sessions() as session:
            record = session.get(MarketRadarSyncRunRecord, run_id)
            return None if record is None else self._to_sync_run(record)

    def latest_complete_run(self) -> MarketRadarSyncRun | None:
        """返回最近完成的同步运行; 失败记录不会替代它。"""
        with self._sessions() as session:
            record = session.scalar(
                select(MarketRadarSyncRunRecord)
                .where(MarketRadarSyncRunRecord.status == "COMPLETE")
                .order_by(MarketRadarSyncRunRecord.completed_at_utc.desc())
                .limit(1)
            )
            return None if record is None else self._to_sync_run(record)

    @staticmethod
    def _to_sync_run(record: MarketRadarSyncRunRecord) -> MarketRadarSyncRun:
        status = record.status
        if status not in _SYNC_RUN_STATUSES:
            raise RuntimeError(f"invalid market radar sync status: {status}")
        started_at = _loaded_utc(record.started_at_utc)
        if started_at is None:
            raise RuntimeError("market radar sync run is missing started_at_utc")
        return MarketRadarSyncRun(
            run_id=record.run_id,
            source=record.source,
            status=cast(SyncRunStatus, status),
            started_at_utc=started_at,
            completed_at_utc=_loaded_utc(record.completed_at_utc),
            requested_start_date=record.requested_start_date,
            requested_end_date=record.requested_end_date,
            instrument_count=record.instrument_count,
            instruments_processed=record.instruments_processed,
            bars_fetched=record.bars_fetched,
            bars_written=record.bars_written,
            error_summary=record.error_summary,
        )

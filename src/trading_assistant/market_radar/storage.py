"""市场雷达独立 SQLite 仓储。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal, cast

from sqlalchemy import create_engine, select, update
from sqlalchemy.engine import CursorResult, Engine
from sqlalchemy.orm import Session, sessionmaker

from trading_assistant.market_radar.metrics import PriceRadarSnapshot
from trading_assistant.market_radar.models import (
    MarketRadarBase,
    MarketRadarSyncRunRecord,
    PriceSnapshotRecord,
)

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

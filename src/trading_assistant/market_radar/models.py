"""市场雷达独立数据库模型。"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class MarketRadarBase(DeclarativeBase):
    """只包含市场雷达表的声明式基类。"""


class MarketRadarSyncRunRecord(MarketRadarBase):
    """一次价格监测数据同步运行。"""

    __tablename__ = "sync_runs"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), index=True)
    started_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at_utc: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    requested_start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    requested_end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    instrument_count: Mapped[int] = mapped_column(Integer)
    instruments_processed: Mapped[int] = mapped_column(Integer, default=0)
    bars_fetched: Mapped[int] = mapped_column(Integer, default=0)
    bars_written: Mapped[int] = mapped_column(Integer, default=0)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)


class PriceSnapshotRecord(MarketRadarBase):
    """由一次成功同步原子发布的价格雷达快照。"""

    __tablename__ = "price_snapshots"

    as_of_date: Mapped[date] = mapped_column(Date, primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("sync_runs.run_id"),
        unique=True,
        index=True,
    )
    calculated_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON)


class CurrentMarketMemberRecord(MarketRadarBase):
    """一次已发布当前成员快照中的最小规范成员。"""

    __tablename__ = "current_market_members"

    membership_date: Mapped[date] = mapped_column(Date, primary_key=True)
    instrument_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("sync_runs.run_id"), index=True)
    source: Mapped[str] = mapped_column(String(64))
    source_symbol: Mapped[str] = mapped_column(String(16))
    data_symbol: Mapped[str] = mapped_column(String(32))


class CurrentBreadthSnapshotRecord(MarketRadarBase):
    """由一次成功同步原子发布的当前宽度快照。"""

    __tablename__ = "current_breadth_snapshots"

    as_of_date: Mapped[date] = mapped_column(Date, primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("sync_runs.run_id"),
        unique=True,
        index=True,
    )
    membership_date: Mapped[date] = mapped_column(Date, index=True)
    calculated_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON)


class RiskAppetiteSnapshotRecord(MarketRadarBase):
    """由一次成功同步原子发布的风险偏好快照。"""

    __tablename__ = "risk_appetite_snapshots"

    as_of_date: Mapped[date] = mapped_column(Date, primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("sync_runs.run_id"),
        unique=True,
        index=True,
    )
    calculated_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON)


class MacroObservationRecord(MarketRadarBase):
    """FRED 当前修订口径的宏观观测。"""

    __tablename__ = "macro_observations"

    source: Mapped[str] = mapped_column(String(16), primary_key=True)
    series_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    observation_date: Mapped[date] = mapped_column(Date, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("sync_runs.run_id"), index=True)
    value: Mapped[float] = mapped_column(Float)
    realtime_start: Mapped[date] = mapped_column(Date)
    realtime_end: Mapped[date] = mapped_column(Date)
    ingested_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MacroRegimeSnapshotRecord(MarketRadarBase):
    """由宏观同步原子发布的四象限快照。"""

    __tablename__ = "macro_regime_snapshots"

    as_of_date: Mapped[date] = mapped_column(Date, primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("sync_runs.run_id"),
        unique=True,
        index=True,
    )
    calculated_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON)


class EarningsMarketMemberRecord(MarketRadarBase):
    """一次盈利同步采用的权威市场成员与可空行业分类。"""

    __tablename__ = "earnings_market_members"

    as_of_date: Mapped[date] = mapped_column(Date, primary_key=True)
    instrument_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("sync_runs.run_id"), index=True)
    membership_date: Mapped[date] = mapped_column(Date, index=True)
    membership_source: Mapped[str] = mapped_column(String(64))
    source_symbol: Mapped[str] = mapped_column(String(16))
    data_symbol: Mapped[str] = mapped_column(String(32))
    sector: Mapped[str | None] = mapped_column(String(32), nullable=True)
    classification_source: Mapped[str] = mapped_column(String(64))
    ingested_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EarningsTrendObservationRecord(MarketRadarBase):
    """一次每日采集中请求集合内每只标的的最新 FY1 预期。"""

    __tablename__ = "earnings_trend_observations"

    as_of_date: Mapped[date] = mapped_column(Date, primary_key=True)
    instrument_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("sync_runs.run_id"), index=True)
    fiscal_period_end: Mapped[date] = mapped_column(Date)
    eps_current: Mapped[float | None] = mapped_column(Float, nullable=True)
    eps_30_days_ago: Mapped[float | None] = mapped_column(Float, nullable=True)
    analyst_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    revisions_up_30_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    revisions_down_30_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    available_at_utc: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    ingested_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EarningsCalendarEventRecord(MarketRadarBase):
    """一次每日采集中 watchlist 的历史与未来财报事件。"""

    __tablename__ = "earnings_calendar_events"

    as_of_date: Mapped[date] = mapped_column(Date, primary_key=True)
    instrument_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    report_date: Mapped[date] = mapped_column(Date, primary_key=True)
    fiscal_period_end: Mapped[date] = mapped_column(Date, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("sync_runs.run_id"), index=True)
    session: Mapped[str] = mapped_column(String(16))
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    actual_eps: Mapped[float | None] = mapped_column(Float, nullable=True)
    estimated_eps: Mapped[float | None] = mapped_column(Float, nullable=True)
    available_at_utc: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    ingested_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EarningsRevisionSnapshotRecord(MarketRadarBase):
    """由一次成功同步原子发布的统一盈利修正快照。"""

    __tablename__ = "earnings_revision_snapshots"

    as_of_date: Mapped[date] = mapped_column(Date, primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("sync_runs.run_id"),
        unique=True,
        index=True,
    )
    calculated_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON)

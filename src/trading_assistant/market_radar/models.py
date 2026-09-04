"""市场雷达独立数据库模型。"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, Date, DateTime, ForeignKey, Integer, String, Text
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

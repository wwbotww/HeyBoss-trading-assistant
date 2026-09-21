"""业务审计数据的 SQLAlchemy 模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """全部业务表的声明式基类。"""


class BacktestRunRecord(Base):
    """一次回测运行。"""

    __tablename__ = "backtest_runs"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32))
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class SignalRecord(Base):
    """按标的展开的目标仓位信号。"""

    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(64), index=True)
    timestamp_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    strategy_name: Mapped[str] = mapped_column(String(128))
    instrument_id: Mapped[str] = mapped_column(String(128))
    direction: Mapped[str] = mapped_column(String(16))
    quantity: Mapped[float | None] = mapped_column(Float, nullable=True)
    target_weight: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(Text)


class SignalWorkflowRecord(Base):
    """跨进程持久化的信号审批与执行工作流。"""

    __tablename__ = "signal_workflows"
    __table_args__ = (
        UniqueConstraint(
            "scope",
            "strategy_name",
            "rebalance_key",
            name="uq_signal_workflow_rebalance",
        ),
    )

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    scope: Mapped[str] = mapped_column(String(128))
    strategy_name: Mapped[str] = mapped_column(String(128))
    rebalance_key: Mapped[str] = mapped_column(String(32))
    target_weights: Mapped[list[list[str | float]]] = mapped_column(JSON)
    preserve_positions: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    factor_context: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    not_before_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reason: Mapped[str] = mapped_column(Text)
    signal_timestamp_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    planned_orders: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    risk_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    telegram_chat_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    telegram_message_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class FactorImportRecord(Base):
    """一次本地交付验收; 与历史模拟可用时间分开保存。"""

    __tablename__ = "factor_imports"
    __table_args__ = (UniqueConstraint("catalog_path", "delivery_id", "mode"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    catalog_path: Mapped[str] = mapped_column(Text)
    delivery_id: Mapped[str] = mapped_column(String(64))
    mode: Mapped[str] = mapped_column(String(16))
    model_release_id: Mapped[str] = mapped_column(String(64))
    calendar_version: Mapped[str] = mapped_column(String(128))
    source_created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class FactorDecisionRecord(Base):
    """无需构造交易事件的因子跳过、保护及恢复审计。"""

    __tablename__ = "factor_decisions"
    __table_args__ = (UniqueConstraint("scope", "strategy_name", "asof_date", "reason"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scope: Mapped[str] = mapped_column(String(128), index=True)
    strategy_name: Mapped[str] = mapped_column(String(128))
    asof_date: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(Text)
    preserve_positions: Mapped[list[str]] = mapped_column(JSON)
    context: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recovered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ApprovalRecord(Base):
    """风控与审批结果。"""

    __tablename__ = "approvals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(64), index=True)
    timestamp_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    strategy_name: Mapped[str] = mapped_column(String(128))
    approval_mode: Mapped[str] = mapped_column(String(16))
    decision: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str] = mapped_column(Text)


class OrderEventRecord(Base):
    """订单生命周期事件。"""

    __tablename__ = "order_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(64), index=True)
    order_event_id: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    timestamp_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    strategy_name: Mapped[str] = mapped_column(String(128))
    instrument_id: Mapped[str] = mapped_column(String(128))
    client_order_id: Mapped[str] = mapped_column(String(128), index=True)
    venue_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32))
    direction: Mapped[str] = mapped_column(String(16))
    quantity: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(Text)


class FillRecord(Base):
    """逐笔成交。"""

    __tablename__ = "fills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("backtest_runs.run_id"), nullable=True, index=True
    )
    event_id: Mapped[str] = mapped_column(String(64), index=True)
    trade_id: Mapped[str] = mapped_column(String(128), unique=True)
    timestamp_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    strategy_name: Mapped[str] = mapped_column(String(128))
    instrument_id: Mapped[str] = mapped_column(String(128))
    client_order_id: Mapped[str] = mapped_column(String(128))
    direction: Mapped[str] = mapped_column(String(16))
    quantity: Mapped[float] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float)
    commission: Mapped[float] = mapped_column(Float)


class TelegramDeliveryRecord(Base):
    """Telegram 订单终态通知的去重记录。"""

    __tablename__ = "telegram_deliveries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_key: Mapped[str] = mapped_column(String(128), unique=True)
    chat_id: Mapped[str] = mapped_column(String(64))
    message_id: Mapped[str] = mapped_column(String(64))
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AccountSnapshotRecord(Base):
    """TradingNode 从 NT 账户接口采集的只读资金快照。"""

    __tablename__ = "account_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    account_id: Mapped[str] = mapped_column(String(128), index=True)
    currency: Mapped[str] = mapped_column(String(16))
    net_liquidation: Mapped[float] = mapped_column(Float)
    available_funds: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_cash_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    account_updated_at_utc: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    broker_connected: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    reconciliation_complete: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    broker_stale_after_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    not_ready_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class PositionSnapshotRecord(Base):
    """与账户快照同一时点的 NT 开仓仓位。"""

    __tablename__ = "position_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_id",
            "instrument_id",
            name="uq_position_snapshot_instrument",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("account_snapshots.id"),
        index=True,
    )
    instrument_id: Mapped[str] = mapped_column(String(128))
    signed_quantity: Mapped[float] = mapped_column(Float)
    side: Mapped[str] = mapped_column(String(16))
    avg_open_price: Mapped[float] = mapped_column(Float)
    realized_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)

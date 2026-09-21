"""SQLite/SQLAlchemy 审计仓储。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, cast

from nautilus_trader.core.uuid import UUID4
from sqlalchemy import case, create_engine, func, inspect, or_, select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.engine import CursorResult, Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from trading_assistant.execution.events import FactorContext, TradeSignalEvent
from trading_assistant.storage.models import (
    AccountSnapshotRecord,
    ApprovalRecord,
    BacktestRunRecord,
    Base,
    FactorDecisionRecord,
    FactorImportRecord,
    FillRecord,
    OrderEventRecord,
    PositionSnapshotRecord,
    SignalRecord,
    SignalWorkflowRecord,
    TelegramDeliveryRecord,
)

WorkflowStatus = Literal[
    "NEW",
    "PENDING",
    "APPROVED",
    "DENIED",
    "EXPIRED",
    "RISK_REJECTED",
    "PROCESSING",
    "ORDERS_SUBMITTED",
]

# 券商成交只有秒精度; 更精细或迟到的早期状态不能覆盖成交与终态。
ORDER_STATUS_PRIORITY = {
    "FILLED": 3,
    "CANCELED": 2,
    "EXPIRED": 2,
    "REJECTED": 2,
    "DENIED": 2,
    "PARTIALLY_FILLED": 1,
}


def utc_datetime_from_ns(timestamp_ns: int) -> datetime:
    """把 Unix 纳秒转换为 UTC datetime。"""
    seconds, nanos = divmod(timestamp_ns, 1_000_000_000)
    return datetime.fromtimestamp(seconds, tz=UTC) + timedelta(microseconds=nanos // 1000)


def utc_ns_from_datetime(value: datetime) -> int:
    """恢复数据库的微秒精度时刻, 避免浮点舍入把过期边界推到未来。"""
    delta = value.astimezone(UTC) - datetime(1970, 1, 1, tzinfo=UTC)
    return (delta.days * 86_400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1_000


@dataclass(frozen=True)
class FillAudit:
    """脱离 SQLAlchemy Session 的只读成交记录。"""

    trade_id: str
    timestamp_utc: datetime
    instrument_id: str
    client_order_id: str
    direction: str
    quantity: float
    price: float
    commission: float
    event_id: str = ""
    strategy_name: str = ""


@dataclass(frozen=True)
class SignalWorkflow:
    """脱离 Session 的审批工作流快照。"""

    event_id: str
    scope: str
    strategy_name: str
    rebalance_key: str
    target_weights: tuple[tuple[str, float], ...]
    reason: str
    signal_timestamp_utc: datetime
    expires_at_utc: datetime
    status: str
    planned_orders: tuple[dict[str, Any], ...]
    risk_summary: str | None
    telegram_chat_id: str | None
    telegram_message_id: str | None
    preserve_positions: tuple[str, ...] = ()
    not_before_ns: int = 0
    factor_context: FactorContext | None = None

    def to_event(self) -> TradeSignalEvent:
        """恢复 Gateway 可消费的不可变 NT 领域事件。"""
        ts_event = utc_ns_from_datetime(self.signal_timestamp_utc)
        return TradeSignalEvent(
            strategy_name=self.strategy_name,
            target_weights=self.target_weights,
            rebalance_key=self.rebalance_key,
            reason=self.reason,
            expires_at_ns=utc_ns_from_datetime(self.expires_at_utc),
            ts_event=ts_event,
            ts_init=ts_event,
            event_id=UUID4.from_str(self.event_id),
            preserve_positions=self.preserve_positions,
            not_before_ns=self.not_before_ns,
            factor_context=self.factor_context,
        )


@dataclass(frozen=True)
class FactorImportAudit:
    """不可变的成功验收凭据。"""

    delivery_id: str
    model_release_id: str
    calendar_version: str
    verified_at: datetime


@dataclass(frozen=True)
class FactorDecisionAudit:
    """可供 Gateway、通知和只读界面使用的因子状态。"""

    id: int
    scope: str
    strategy_name: str
    asof_date: str
    status: str
    reason: str
    preserve_positions: tuple[str, ...]
    context: FactorContext | None
    first_seen: datetime
    last_seen: datetime
    recovered_at: datetime | None


@dataclass(frozen=True)
class OrderNotification:
    """待发送 Telegram 的订单终态。"""

    source_key: str
    event_id: str
    timestamp_utc: datetime
    instrument_id: str
    client_order_id: str
    status: str
    direction: str
    quantity: float
    reason: str


@dataclass(frozen=True)
class WorkflowAlertNotification:
    """待发送 Telegram 的工作流终态提醒。"""

    source_key: str
    workflow: SignalWorkflow


@dataclass(frozen=True)
class PositionSnapshotInput:
    """待持久化的 NT 开仓仓位。"""

    instrument_id: str
    signed_quantity: float
    side: str
    avg_open_price: float
    realized_pnl: float | None


@dataclass(frozen=True)
class PositionSnapshot:
    """脱离 Session 的只读仓位快照。"""

    instrument_id: str
    signed_quantity: float
    side: str
    avg_open_price: float
    realized_pnl: float | None


@dataclass(frozen=True)
class PortfolioSnapshot:
    """账户资金与同一采样时点仓位的不可变快照。"""

    timestamp_utc: datetime
    account_id: str
    currency: str
    net_liquidation: float
    available_funds: float | None
    total_cash_value: float | None
    positions: tuple[PositionSnapshot, ...]
    account_updated_at_utc: datetime | None = None
    broker_connected: bool | None = None
    reconciliation_complete: bool | None = None
    broker_stale_after_seconds: int | None = None
    not_ready_reason: str | None = None


@dataclass(frozen=True)
class AccountSnapshotAudit:
    """脱离 Session 的只读账户资金快照。"""

    timestamp_utc: datetime
    account_id: str
    currency: str
    net_liquidation: float
    available_funds: float | None
    total_cash_value: float | None
    account_updated_at_utc: datetime | None = None
    broker_connected: bool | None = None
    reconciliation_complete: bool | None = None
    broker_stale_after_seconds: int | None = None
    not_ready_reason: str | None = None


@dataclass(frozen=True)
class SignalReview:
    """信号与工作流关联后的只读审计视图。"""

    event_id: str
    timestamp_utc: datetime
    strategy_name: str
    instrument_id: str
    direction: str
    target_weight: float
    reason: str
    status: str
    planned_orders: tuple[dict[str, Any], ...]
    risk_summary: str | None


@dataclass(frozen=True)
class DecisionAudit:
    """风控或人工审批审计事件。"""

    timestamp_utc: datetime
    event_id: str
    strategy_name: str
    approval_mode: str
    decision: str
    reason: str


@dataclass(frozen=True)
class OrderAudit:
    """订单生命周期审计事件。"""

    timestamp_utc: datetime
    event_id: str
    instrument_id: str
    client_order_id: str
    status: str
    direction: str
    quantity: float
    reason: str
    venue_order_id: str | None = None


@dataclass(frozen=True)
class BacktestRunAudit:
    """脱离 Session 的回测运行快照。"""

    run_id: str
    started_at: datetime
    completed_at: datetime | None
    status: str
    summary: dict[str, Any] | None


class TradingRepository:
    """记录信号、审批、订单、成交和回测运行。"""

    def __init__(self, database_url: str, *, read_only: bool = False) -> None:
        if not read_only:
            self._ensure_sqlite_parent(database_url)
        connect_args: dict[str, Any] = {}
        if database_url.startswith("sqlite:"):
            connect_args["timeout"] = 0.05
        effective_url = self._read_only_url(database_url) if read_only else database_url
        self._engine: Engine = create_engine(effective_url, connect_args=connect_args)
        self._sessions = sessionmaker(self._engine, expire_on_commit=False)

    @staticmethod
    def _read_only_url(database_url: str) -> str:
        """把本地 SQLite URL 转为数据库强制的只读 URI。"""
        prefix = "sqlite:///"
        if not database_url.startswith(prefix):
            raise ValueError("Read-only repository currently requires a SQLite database")
        database_path = database_url.removeprefix(prefix)
        if database_path == ":memory:":
            raise ValueError("Read-only repository cannot use an in-memory database")
        absolute_path = Path(database_path).expanduser().resolve()
        return f"sqlite:///file:{absolute_path}?mode=ro&uri=true"

    @staticmethod
    def _ensure_sqlite_parent(database_url: str) -> None:
        prefix = "sqlite:///"
        if not database_url.startswith(prefix):
            return
        database_path = database_url.removeprefix(prefix)
        if database_path == ":memory:":
            return
        Path(database_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)

    def create_schema(self) -> None:
        """幂等创建业务表。"""
        inspector = inspect(self._engine)
        if inspector.has_table("signal_workflows"):
            columns = {column["name"] for column in inspector.get_columns("signal_workflows")}
            if not {"preserve_positions", "factor_context", "not_before_utc"} <= columns:
                raise RuntimeError(
                    "Factor protection migration required; run migrate_factor_protection.py"
                )
        for table_name, columns in {
            "account_snapshots": {
                "account_updated_at_utc",
                "total_cash_value",
                "available_funds",
                "broker_connected",
                "reconciliation_complete",
                "broker_stale_after_seconds",
                "not_ready_reason",
            },
            "order_events": {"venue_order_id"},
        }.items():
            if inspector.has_table(table_name) and not columns <= {
                column["name"] for column in inspector.get_columns(table_name)
            }:
                raise RuntimeError(
                    "Execution audit migration required; run migrate_execution_audit.py"
                )
        Base.metadata.create_all(self._engine)

    def verify_schema(self) -> None:
        """交易启动只验证完整运行库; 不在启动时隐式迁移或新建旧库。"""
        inspector = inspect(self._engine)
        for table in Base.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                raise RuntimeError(f"Runtime database is missing table: {table.name}")
            actual = {column["name"] for column in inspector.get_columns(table.name)}
            missing = set(table.columns.keys()) - actual
            if missing:
                raise RuntimeError(
                    f"Runtime database migration required: {table.name}: {sorted(missing)}"
                )

    def get_factor_import(
        self, *, catalog_path: str, delivery_id: str, mode: str
    ) -> FactorImportAudit | None:
        """只读取同一 Catalog、模式和交付的成功验收。"""
        with Session(self._engine) as session:
            row = session.scalar(
                select(FactorImportRecord).where(
                    FactorImportRecord.catalog_path == catalog_path,
                    FactorImportRecord.delivery_id == delivery_id,
                    FactorImportRecord.mode == mode,
                )
            )
            if row is None:
                return None
            return FactorImportAudit(
                row.delivery_id,
                row.model_release_id,
                row.calendar_version,
                self._as_utc(row.verified_at),
            )

    def record_factor_import(
        self,
        *,
        catalog_path: str,
        delivery_id: str,
        mode: str,
        model_release_id: str,
        calendar_version: str,
        source_created_at: datetime,
        verified_at: datetime,
    ) -> FactorImportAudit:
        """幂等保留首次成功验收时刻, 不通过重复导入倒填时间。"""
        existing = self.get_factor_import(
            catalog_path=catalog_path, delivery_id=delivery_id, mode=mode
        )
        if existing is not None:
            return existing
        try:
            with self._sessions.begin() as session:
                session.add(
                    FactorImportRecord(
                        catalog_path=catalog_path,
                        delivery_id=delivery_id,
                        mode=mode,
                        model_release_id=model_release_id,
                        calendar_version=calendar_version,
                        source_created_at=source_created_at,
                        verified_at=verified_at,
                    )
                )
        except IntegrityError:
            concurrent = self.get_factor_import(
                catalog_path=catalog_path, delivery_id=delivery_id, mode=mode
            )
            if concurrent is None:
                raise
            return concurrent
        return FactorImportAudit(delivery_id, model_release_id, calendar_version, verified_at)

    def record_factor_decision(
        self,
        *,
        scope: str,
        strategy_name: str,
        asof_date: str,
        status: str,
        reason: str,
        timestamp_ns: int,
        preserve_positions: tuple[str, ...] = (),
        context: FactorContext | None = None,
    ) -> None:
        """按交易日及原因合并重复检查, 恢复时保留原失败审计。"""
        now = utc_datetime_from_ns(timestamp_ns)
        with self._sessions.begin() as session:
            row = session.scalar(
                select(FactorDecisionRecord).where(
                    FactorDecisionRecord.scope == scope,
                    FactorDecisionRecord.strategy_name == strategy_name,
                    FactorDecisionRecord.asof_date == asof_date,
                    FactorDecisionRecord.reason == reason,
                )
            )
            if row is None:
                row = FactorDecisionRecord(
                    scope=scope,
                    strategy_name=strategy_name,
                    asof_date=asof_date,
                    status=status,
                    reason=reason,
                    first_seen=now,
                    last_seen=now,
                    preserve_positions=list(preserve_positions),
                    context=None if context is None else context.to_payload(),
                )
                session.add(row)
            else:
                row.last_seen = now
                row.status = status
                row.preserve_positions = list(preserve_positions)
                row.context = None if context is None else context.to_payload()
                row.recovered_at = None
            if status == "REBALANCE":
                session.execute(
                    update(FactorDecisionRecord)
                    .where(
                        FactorDecisionRecord.scope == scope,
                        FactorDecisionRecord.strategy_name == strategy_name,
                        FactorDecisionRecord.asof_date == asof_date,
                        FactorDecisionRecord.status == "SKIP",
                        ~FactorDecisionRecord.reason.like("execution:%"),
                        FactorDecisionRecord.recovered_at.is_(None),
                    )
                    .values(recovered_at=now)
                )

    def list_factor_decisions(
        self,
        *,
        scope: str,
        strategy_name: str | None = None,
        asof_date: str | None = None,
        limit: int = 200,
    ) -> tuple[FactorDecisionAudit, ...]:
        """读取本运行的最近因子状态和恢复信息。"""
        query = select(FactorDecisionRecord).where(FactorDecisionRecord.scope == scope)
        if strategy_name is not None:
            query = query.where(FactorDecisionRecord.strategy_name == strategy_name)
        if asof_date is not None:
            query = query.where(FactorDecisionRecord.asof_date == asof_date)
        with Session(self._engine) as session:
            rows = session.scalars(
                query.order_by(
                    FactorDecisionRecord.last_seen.desc(), FactorDecisionRecord.id.desc()
                ).limit(limit)
            )
            return tuple(
                FactorDecisionAudit(
                    row.id,
                    row.scope,
                    row.strategy_name,
                    row.asof_date,
                    row.status,
                    row.reason,
                    tuple(row.preserve_positions),
                    None if row.context is None else FactorContext.from_payload(row.context),
                    self._as_utc(row.first_seen),
                    self._as_utc(row.last_seen),
                    None if row.recovered_at is None else self._as_utc(row.recovered_at),
                )
                for row in rows
            )

    def close(self) -> None:
        """释放数据库连接池。"""
        self._engine.dispose()

    def healthcheck(self) -> None:
        """执行不依赖业务表的只读连接检查。"""
        with Session(self._engine) as session:
            session.scalar(select(1))

    def record_portfolio_snapshot(
        self,
        *,
        timestamp_ns: int,
        account_id: str,
        currency: str,
        net_liquidation: float,
        available_funds: float | None,
        total_cash_value: float | None,
        positions: tuple[PositionSnapshotInput, ...],
        account_updated_at_utc: datetime | None = None,
        broker_connected: bool | None = None,
        reconciliation_complete: bool | None = None,
        broker_stale_after_seconds: int | None = None,
        not_ready_reason: str | None = None,
    ) -> None:
        """原子记录一次 NT 账户资金与全部开仓仓位。"""
        with self._sessions.begin() as session:
            account = AccountSnapshotRecord(
                timestamp_utc=utc_datetime_from_ns(timestamp_ns),
                account_id=account_id,
                currency=currency,
                net_liquidation=net_liquidation,
                available_funds=available_funds,
                total_cash_value=total_cash_value,
                account_updated_at_utc=account_updated_at_utc,
                broker_connected=broker_connected,
                reconciliation_complete=reconciliation_complete,
                broker_stale_after_seconds=broker_stale_after_seconds,
                not_ready_reason=not_ready_reason,
            )
            session.add(account)
            session.flush()
            session.add_all(
                PositionSnapshotRecord(
                    snapshot_id=account.id,
                    instrument_id=position.instrument_id,
                    signed_quantity=position.signed_quantity,
                    side=position.side,
                    avg_open_price=position.avg_open_price,
                    realized_pnl=position.realized_pnl,
                )
                for position in positions
            )

    def latest_portfolio_snapshot(self, *, account_id: str) -> PortfolioSnapshot | None:
        """读取指定账户最近一次完整快照。"""
        with Session(self._engine) as session:
            account = session.scalar(
                select(AccountSnapshotRecord)
                .where(AccountSnapshotRecord.account_id == account_id)
                .order_by(
                    AccountSnapshotRecord.timestamp_utc.desc(), AccountSnapshotRecord.id.desc()
                )
                .limit(1)
            )
            if account is None:
                return None
            rows = session.scalars(
                select(PositionSnapshotRecord)
                .where(PositionSnapshotRecord.snapshot_id == account.id)
                .order_by(PositionSnapshotRecord.instrument_id)
            )
            positions = tuple(
                PositionSnapshot(
                    instrument_id=row.instrument_id,
                    signed_quantity=row.signed_quantity,
                    side=row.side,
                    avg_open_price=row.avg_open_price,
                    realized_pnl=row.realized_pnl,
                )
                for row in rows
            )
            return PortfolioSnapshot(
                timestamp_utc=self._as_utc(account.timestamp_utc),
                account_id=account.account_id,
                currency=account.currency,
                net_liquidation=account.net_liquidation,
                available_funds=account.available_funds,
                total_cash_value=account.total_cash_value,
                broker_connected=account.broker_connected,
                reconciliation_complete=account.reconciliation_complete,
                broker_stale_after_seconds=account.broker_stale_after_seconds,
                not_ready_reason=account.not_ready_reason,
                account_updated_at_utc=(
                    None
                    if account.account_updated_at_utc is None
                    else self._as_utc(account.account_updated_at_utc)
                ),
                positions=positions,
            )

    def list_account_snapshots(
        self,
        *,
        account_id: str,
        limit: int = 500,
        offset: int = 0,
    ) -> tuple[AccountSnapshotAudit, ...]:
        """按时间倒序读取指定账户的资金历史。"""
        with Session(self._engine) as session:
            rows = session.scalars(
                select(AccountSnapshotRecord)
                .where(AccountSnapshotRecord.account_id == account_id)
                .order_by(
                    AccountSnapshotRecord.timestamp_utc.desc(),
                    AccountSnapshotRecord.id.desc(),
                )
                .offset(offset)
                .limit(limit)
            )
            return tuple(
                AccountSnapshotAudit(
                    timestamp_utc=self._as_utc(row.timestamp_utc),
                    account_id=row.account_id,
                    currency=row.currency,
                    net_liquidation=row.net_liquidation,
                    available_funds=row.available_funds,
                    total_cash_value=row.total_cash_value,
                    broker_connected=row.broker_connected,
                    reconciliation_complete=row.reconciliation_complete,
                    broker_stale_after_seconds=row.broker_stale_after_seconds,
                    not_ready_reason=row.not_ready_reason,
                    account_updated_at_utc=(
                        None
                        if row.account_updated_at_utc is None
                        else self._as_utc(row.account_updated_at_utc)
                    ),
                )
                for row in rows
            )

    def list_signal_workflows(
        self,
        *,
        scope: str,
        limit: int = 500,
        offset: int = 0,
        status: str | None = None,
    ) -> tuple[SignalWorkflow, ...]:
        """按更新时间倒序读取指定作用域的工作流。"""
        statement = select(SignalWorkflowRecord).where(SignalWorkflowRecord.scope == scope)
        if status is not None:
            statement = statement.where(SignalWorkflowRecord.status == status)
        with Session(self._engine) as session:
            rows = session.scalars(
                statement.order_by(
                    SignalWorkflowRecord.updated_at.desc(),
                    SignalWorkflowRecord.event_id.desc(),
                )
                .offset(offset)
                .limit(limit)
            )
            return tuple(self._workflow_snapshot(row) for row in rows)

    def workflow_status_counts(self, *, scope: str) -> dict[str, int]:
        """聚合指定作用域的全部工作流状态。"""
        with Session(self._engine) as session:
            rows = session.execute(
                select(SignalWorkflowRecord.status, func.count(SignalWorkflowRecord.event_id))
                .where(SignalWorkflowRecord.scope == scope)
                .group_by(SignalWorkflowRecord.status)
            )
            return {str(status): int(count) for status, count in rows}

    def list_signal_reviews(
        self,
        *,
        scope: str,
        limit: int = 500,
        offset: int = 0,
        status: str | None = None,
        instrument_id: str | None = None,
    ) -> tuple[SignalReview, ...]:
        """按时间倒序读取指定作用域的逐标的信号。"""
        statement = (
            select(SignalRecord, SignalWorkflowRecord)
            .join(
                SignalWorkflowRecord,
                SignalWorkflowRecord.event_id == SignalRecord.event_id,
            )
            .where(SignalWorkflowRecord.scope == scope)
        )
        if status is not None:
            statement = statement.where(SignalWorkflowRecord.status == status)
        if instrument_id is not None:
            statement = statement.where(SignalRecord.instrument_id == instrument_id)
        with Session(self._engine) as session:
            rows = session.execute(
                statement.order_by(SignalRecord.timestamp_utc.desc(), SignalRecord.id.desc())
                .offset(offset)
                .limit(limit)
            )
            return tuple(
                SignalReview(
                    event_id=signal.event_id,
                    timestamp_utc=self._as_utc(signal.timestamp_utc),
                    strategy_name=signal.strategy_name,
                    instrument_id=signal.instrument_id,
                    direction=signal.direction,
                    target_weight=signal.target_weight,
                    reason=signal.reason,
                    status=workflow.status,
                    planned_orders=tuple(workflow.planned_orders or ()),
                    risk_summary=workflow.risk_summary,
                )
                for signal, workflow in rows
            )

    def list_decision_audits(
        self,
        *,
        scope: str,
        limit: int = 500,
        offset: int = 0,
        event_id: str | None = None,
    ) -> tuple[DecisionAudit, ...]:
        """按时间倒序读取指定作用域的风控与审批事件。"""
        statement = (
            select(ApprovalRecord)
            .join(
                SignalWorkflowRecord,
                SignalWorkflowRecord.event_id == ApprovalRecord.event_id,
            )
            .where(SignalWorkflowRecord.scope == scope)
        )
        if event_id is not None:
            statement = statement.where(ApprovalRecord.event_id == event_id)
        with Session(self._engine) as session:
            rows = session.scalars(
                statement.order_by(ApprovalRecord.timestamp_utc.desc(), ApprovalRecord.id.desc())
                .offset(offset)
                .limit(limit)
            )
            return tuple(
                DecisionAudit(
                    timestamp_utc=self._as_utc(row.timestamp_utc),
                    event_id=row.event_id,
                    strategy_name=row.strategy_name,
                    approval_mode=row.approval_mode,
                    decision=row.decision,
                    reason=row.reason,
                )
                for row in rows
            )

    def list_order_audits(
        self,
        *,
        scope: str,
        limit: int = 500,
        offset: int = 0,
        status: str | None = None,
        instrument_id: str | None = None,
        event_id: str | None = None,
        client_order_id: str | None = None,
        client_order_ids: tuple[str, ...] | None = None,
    ) -> tuple[OrderAudit, ...]:
        """按时间倒序读取指定作用域的订单生命周期。"""
        statement = (
            select(OrderEventRecord)
            .join(
                SignalWorkflowRecord,
                SignalWorkflowRecord.event_id == OrderEventRecord.event_id,
            )
            .where(SignalWorkflowRecord.scope == scope)
        )
        if status is not None:
            statement = statement.where(OrderEventRecord.status == status)
        if instrument_id is not None:
            statement = statement.where(OrderEventRecord.instrument_id == instrument_id)
        if event_id is not None:
            statement = statement.where(OrderEventRecord.event_id == event_id)
        if client_order_id is not None:
            statement = statement.where(OrderEventRecord.client_order_id == client_order_id)
        if client_order_ids is not None:
            statement = statement.where(OrderEventRecord.client_order_id.in_(client_order_ids))
        with Session(self._engine) as session:
            rows = session.scalars(
                statement.order_by(
                    OrderEventRecord.timestamp_utc.desc(),
                    OrderEventRecord.id.desc(),
                )
                .offset(offset)
                .limit(limit)
            )
            return tuple(self._order_audit_snapshot(row) for row in rows)

    def list_latest_order_audits(
        self,
        *,
        scope: str,
        limit: int = 500,
        offset: int = 0,
        status: str | None = None,
        instrument_id: str | None = None,
    ) -> tuple[OrderAudit, ...]:
        """按状态推进优先、事件时间次序返回每个订单的当前状态。"""
        ranked = (
            select(
                OrderEventRecord.id.label("order_event_id"),
                func.row_number()
                .over(
                    partition_by=OrderEventRecord.client_order_id,
                    order_by=(
                        case(ORDER_STATUS_PRIORITY, value=OrderEventRecord.status, else_=0).desc(),
                        OrderEventRecord.timestamp_utc.desc(),
                        OrderEventRecord.id.desc(),
                    ),
                )
                .label("row_number"),
            )
            .join(
                SignalWorkflowRecord,
                SignalWorkflowRecord.event_id == OrderEventRecord.event_id,
            )
            .where(SignalWorkflowRecord.scope == scope)
            .subquery()
        )
        statement = (
            select(OrderEventRecord)
            .join(ranked, ranked.c.order_event_id == OrderEventRecord.id)
            .where(ranked.c.row_number == 1)
        )
        if status is not None:
            statement = statement.where(OrderEventRecord.status == status)
        if instrument_id is not None:
            statement = statement.where(OrderEventRecord.instrument_id == instrument_id)
        with Session(self._engine) as session:
            rows = session.scalars(
                statement.order_by(
                    OrderEventRecord.timestamp_utc.desc(),
                    OrderEventRecord.id.desc(),
                )
                .offset(offset)
                .limit(limit)
            )
            return tuple(self._order_audit_snapshot(row) for row in rows)

    def list_fill_audits(
        self,
        *,
        scope: str,
        limit: int = 500,
        offset: int = 0,
        instrument_id: str | None = None,
        event_id: str | None = None,
        client_order_id: str | None = None,
        client_order_ids: tuple[str, ...] | None = None,
    ) -> tuple[FillAudit, ...]:
        """按时间倒序读取指定作用域的 paper/live 成交。"""
        statement = (
            select(FillRecord)
            .join(
                SignalWorkflowRecord,
                SignalWorkflowRecord.event_id == FillRecord.event_id,
            )
            .where(
                SignalWorkflowRecord.scope == scope,
                FillRecord.run_id.is_(None),
            )
        )
        if instrument_id is not None:
            statement = statement.where(FillRecord.instrument_id == instrument_id)
        if event_id is not None:
            statement = statement.where(FillRecord.event_id == event_id)
        if client_order_id is not None:
            statement = statement.where(FillRecord.client_order_id == client_order_id)
        if client_order_ids is not None:
            statement = statement.where(FillRecord.client_order_id.in_(client_order_ids))
        with Session(self._engine) as session:
            rows = session.scalars(
                statement.order_by(FillRecord.timestamp_utc.desc(), FillRecord.id.desc())
                .offset(offset)
                .limit(limit)
            )
            return tuple(
                FillAudit(
                    trade_id=row.trade_id,
                    timestamp_utc=self._as_utc(row.timestamp_utc),
                    instrument_id=row.instrument_id,
                    client_order_id=row.client_order_id,
                    direction=row.direction,
                    quantity=row.quantity,
                    price=row.price,
                    commission=row.commission,
                    event_id=row.event_id,
                    strategy_name=row.strategy_name,
                )
                for row in rows
            )

    def record_signal(self, event: TradeSignalEvent) -> None:
        """按标的展开并记录信号事件。"""
        targets = event.target_weights or (("CASH.USD", 0.0),)
        rows = [
            SignalRecord(
                event_id=str(event.id),
                timestamp_utc=utc_datetime_from_ns(event.ts_event),
                strategy_name=event.strategy_name,
                instrument_id=instrument_id,
                direction="LONG" if weight > 0 else "FLAT",
                quantity=None,
                target_weight=weight,
                reason=event.reason,
            )
            for instrument_id, weight in targets
        ]
        with self._sessions.begin() as session:
            session.add_all(rows)

    def register_signal_workflow(
        self,
        event: TradeSignalEvent,
        *,
        scope: str,
    ) -> tuple[SignalWorkflow, bool]:
        """按作用域、策略和调仓周期幂等注册信号。"""
        existing = self.find_signal_workflow(
            scope=scope,
            strategy_name=event.strategy_name,
            rebalance_key=event.rebalance_key,
        )
        if existing is not None:
            return existing, False

        now = datetime.now(UTC)
        row = SignalWorkflowRecord(
            event_id=str(event.id),
            scope=scope,
            strategy_name=event.strategy_name,
            rebalance_key=event.rebalance_key,
            target_weights=[
                [instrument_id, weight] for instrument_id, weight in event.target_weights
            ],
            reason=event.reason,
            signal_timestamp_utc=utc_datetime_from_ns(event.ts_event),
            preserve_positions=list(event.preserve_positions),
            factor_context=None
            if event.factor_context is None
            else event.factor_context.to_payload(),
            not_before_utc=utc_datetime_from_ns(event.not_before_ns),
            expires_at_utc=utc_datetime_from_ns(event.expires_at_ns),
            status="NEW",
            planned_orders=None,
            risk_summary=None,
            telegram_chat_id=None,
            telegram_message_id=None,
            notified_at=None,
            decided_at=None,
            decision_by=None,
            created_at=now,
            updated_at=now,
        )
        try:
            with self._sessions.begin() as session:
                session.add(row)
        except IntegrityError:
            concurrent = self.find_signal_workflow(
                scope=scope,
                strategy_name=event.strategy_name,
                rebalance_key=event.rebalance_key,
            )
            if concurrent is None:
                raise
            return concurrent, False
        return self._workflow_snapshot(row), True

    def find_signal_workflow(
        self,
        *,
        scope: str,
        strategy_name: str,
        rebalance_key: str,
    ) -> SignalWorkflow | None:
        """按稳定幂等键读取工作流。"""
        with Session(self._engine) as session:
            row = session.scalar(
                select(SignalWorkflowRecord).where(
                    SignalWorkflowRecord.scope == scope,
                    SignalWorkflowRecord.strategy_name == strategy_name,
                    SignalWorkflowRecord.rebalance_key == rebalance_key,
                )
            )
            return None if row is None else self._workflow_snapshot(row)

    def get_signal_workflow(self, event_id: str) -> SignalWorkflow | None:
        """按事件 ID 读取工作流。"""
        with Session(self._engine) as session:
            row = session.get(SignalWorkflowRecord, event_id)
            return None if row is None else self._workflow_snapshot(row)

    def prepare_manual_approval(
        self,
        event_id: str,
        *,
        planned_orders: tuple[dict[str, Any], ...],
        risk_summary: str,
        timestamp_ns: int,
    ) -> bool:
        """原子地把初次风控通过的 NEW 信号送入人工审批。"""
        return self._transition(
            event_id,
            expected_status="NEW",
            new_status="PENDING",
            timestamp_ns=timestamp_ns,
            values={"planned_orders": list(planned_orders), "risk_summary": risk_summary},
        )

    def decide_manual_approval(
        self,
        event_id: str,
        *,
        approved: bool,
        decision_by: str,
        timestamp_ns: int,
    ) -> bool:
        """由 Bot 原子确认或否决一个仍在等待的信号。"""
        return self._transition(
            event_id,
            expected_status="PENDING",
            new_status="APPROVED" if approved else "DENIED",
            timestamp_ns=timestamp_ns,
            values={
                "decided_at": utc_datetime_from_ns(timestamp_ns),
                "decision_by": decision_by,
            },
        )

    def claim_auto_signal(
        self,
        event_id: str,
        *,
        timestamp_ns: int,
        planned_orders: tuple[dict[str, object], ...],
        risk_summary: str,
    ) -> bool:
        """在提交前以同一事务独占信号并保存 auto 批准依据。"""
        now = utc_datetime_from_ns(timestamp_ns)
        with self._sessions.begin() as session:
            changed = session.execute(
                update(SignalWorkflowRecord)
                .where(
                    SignalWorkflowRecord.event_id == event_id,
                    SignalWorkflowRecord.status == "NEW",
                    SignalWorkflowRecord.expires_at_utc > now,
                    or_(
                        SignalWorkflowRecord.not_before_utc.is_(None),
                        SignalWorkflowRecord.not_before_utc <= now,
                    ),
                )
                .values(
                    status="PROCESSING",
                    planned_orders=list(planned_orders),
                    risk_summary=risk_summary,
                    decided_at=now,
                    decision_by="auto",
                    updated_at=now,
                )
                .returning(SignalWorkflowRecord.strategy_name)
            ).scalar_one_or_none()
            if changed is None:
                return False
            session.add(
                ApprovalRecord(
                    event_id=event_id,
                    timestamp_utc=now,
                    strategy_name=changed,
                    approval_mode="auto",
                    decision="APPROVED",
                    reason=risk_summary,
                )
            )
        return True

    def reject_new_signal(
        self,
        event_id: str,
        *,
        status: Literal["EXPIRED", "RISK_REJECTED"],
        timestamp_ns: int,
        risk_summary: str,
    ) -> bool:
        """原子终止一个尚未进入审批的信号。"""
        return self._transition(
            event_id,
            expected_status="NEW",
            new_status=status,
            timestamp_ns=timestamp_ns,
            values={"risk_summary": risk_summary},
        )

    def rearm_terminal_signal(
        self,
        event_id: str,
        *,
        reason: str,
        timestamp_ns: int,
        expires_at_ns: int,
        expected_model_release_id: str | None = None,
    ) -> bool:
        """由操作者原子恢复可重试终态、刷新期限并追加审计记录。"""
        timestamp = utc_datetime_from_ns(timestamp_ns)
        expires_at = utc_datetime_from_ns(expires_at_ns)
        if expires_at <= timestamp:
            raise ValueError("expires_at_ns must be later than timestamp_ns")
        with self._sessions.begin() as session:
            row = session.get(SignalWorkflowRecord, event_id)
            if row is None:
                return False
            if row.strategy_name == "patchtst_e3":
                from trading_assistant.data.factor import expected_factor_date
                from trading_assistant.data.market_calendar import CALENDAR_VERSION

                if row.factor_context is None or row.preserve_positions is None:
                    return False
                context = FactorContext.from_payload(row.factor_context)
                if (
                    expected_model_release_id != context.model_release_id
                    or context.calendar_version != CALENDAR_VERSION
                    or context.asof_date != expected_factor_date(timestamp).isoformat()
                    or expires_at > self._as_utc(row.expires_at_utc)
                    or timestamp >= self._as_utc(row.expires_at_utc)
                    or session.scalar(
                        select(OrderEventRecord.id)
                        .where(OrderEventRecord.event_id == event_id)
                        .limit(1)
                    )
                    is not None
                ):
                    return False
            statement = (
                update(SignalWorkflowRecord)
                .where(
                    SignalWorkflowRecord.event_id == event_id,
                    SignalWorkflowRecord.status.in_(("RISK_REJECTED", "EXPIRED")),
                )
                .values(
                    status="NEW",
                    planned_orders=None,
                    risk_summary=None,
                    telegram_chat_id=None,
                    telegram_message_id=None,
                    notified_at=None,
                    decided_at=None,
                    decision_by=None,
                    expires_at_utc=expires_at,
                    updated_at=timestamp,
                )
            )
            result = cast("CursorResult[Any]", session.execute(statement))
            if result.rowcount != 1:
                return False
            if row.strategy_name == "patchtst_e3" and row.factor_context is not None:
                session.execute(
                    update(FactorDecisionRecord)
                    .where(
                        FactorDecisionRecord.scope == row.scope,
                        FactorDecisionRecord.strategy_name == row.strategy_name,
                        FactorDecisionRecord.asof_date == str(row.factor_context["asof_date"]),
                        FactorDecisionRecord.reason.like("execution:%"),
                    )
                    .values(recovered_at=timestamp)
                )
            session.add(
                ApprovalRecord(
                    event_id=event_id,
                    timestamp_utc=timestamp,
                    strategy_name=row.strategy_name,
                    approval_mode="operator",
                    decision="REARMED",
                    reason=reason,
                )
            )
            return True

    def claim_next_approved(self, *, timestamp_ns: int, scope: str) -> SignalWorkflow | None:
        """由 Gateway 原子领取最早获批的人工信号。"""
        with Session(self._engine) as session:
            event_ids = tuple(
                session.scalars(
                    select(SignalWorkflowRecord.event_id)
                    .where(
                        SignalWorkflowRecord.status == "APPROVED",
                        SignalWorkflowRecord.scope == scope,
                        or_(
                            SignalWorkflowRecord.not_before_utc.is_(None),
                            SignalWorkflowRecord.not_before_utc
                            <= utc_datetime_from_ns(timestamp_ns),
                        ),
                    )
                    .order_by(SignalWorkflowRecord.updated_at, SignalWorkflowRecord.event_id)
                )
            )
        for event_id in event_ids:
            if self._transition(
                event_id,
                expected_status="APPROVED",
                new_status="PROCESSING",
                timestamp_ns=timestamp_ns,
            ):
                return self.get_signal_workflow(event_id)
        return None

    def finish_processing(
        self,
        event_id: str,
        *,
        status: Literal["ORDERS_SUBMITTED", "EXPIRED", "RISK_REJECTED"],
        timestamp_ns: int,
        risk_summary: str | None = None,
    ) -> bool:
        """将已领取信号写入最终网关结果。"""
        values: dict[str, Any] = {}
        if risk_summary is not None:
            values["risk_summary"] = risk_summary
        return self._transition(
            event_id,
            expected_status="PROCESSING",
            new_status=status,
            timestamp_ns=timestamp_ns,
            values=values,
        )

    def list_new_signal_workflows(self, *, scope: str) -> tuple[SignalWorkflow, ...]:
        """返回指定运行作用域内尚未被执行网关处理的信号。"""
        with Session(self._engine) as session:
            rows = session.scalars(
                select(SignalWorkflowRecord)
                .where(
                    SignalWorkflowRecord.scope == scope,
                    SignalWorkflowRecord.status == "NEW",
                )
                .order_by(SignalWorkflowRecord.created_at, SignalWorkflowRecord.event_id)
            )
            return tuple(self._workflow_snapshot(row) for row in rows)

    def list_pending_notifications(self, *, scope: str) -> tuple[SignalWorkflow, ...]:
        """返回指定 paper 作用域内尚未推送的人工审批。"""
        with Session(self._engine) as session:
            rows = session.scalars(
                select(SignalWorkflowRecord)
                .where(
                    SignalWorkflowRecord.scope == scope,
                    SignalWorkflowRecord.status == "PENDING",
                    SignalWorkflowRecord.notified_at.is_(None),
                )
                .order_by(SignalWorkflowRecord.created_at)
            )
            return tuple(self._workflow_snapshot(row) for row in rows)

    def mark_workflow_notified(
        self,
        event_id: str,
        *,
        chat_id: str,
        message_id: str,
        timestamp_ns: int,
    ) -> bool:
        """记录审批卡片位置并避免正常轮询重复发送。"""
        return self._transition(
            event_id,
            expected_status="PENDING",
            new_status="PENDING",
            timestamp_ns=timestamp_ns,
            extra_conditions=(SignalWorkflowRecord.notified_at.is_(None),),
            values={
                "telegram_chat_id": chat_id,
                "telegram_message_id": message_id,
                "notified_at": utc_datetime_from_ns(timestamp_ns),
            },
        )

    def list_workflow_alert_notifications(
        self,
        *,
        scope: str,
    ) -> tuple[WorkflowAlertNotification, ...]:
        """返回指定 paper 作用域内尚未发送的风控拒绝或过期提醒。"""
        with Session(self._engine) as session:
            delivered = frozenset(session.scalars(select(TelegramDeliveryRecord.source_key)))
            rearm_rows = cast(
                "list[tuple[str, int]]",
                session.execute(
                    select(ApprovalRecord.event_id, func.count(ApprovalRecord.id))
                    .where(ApprovalRecord.decision == "REARMED")
                    .group_by(ApprovalRecord.event_id)
                ).all(),
            )
            rearm_counts: dict[str, int] = dict(rearm_rows)
            rows = session.scalars(
                select(SignalWorkflowRecord)
                .where(
                    SignalWorkflowRecord.scope == scope,
                    SignalWorkflowRecord.status.in_(("RISK_REJECTED", "EXPIRED")),
                )
                .order_by(SignalWorkflowRecord.updated_at, SignalWorkflowRecord.event_id)
            )
            notifications: list[WorkflowAlertNotification] = []
            for row in rows:
                source_key = f"workflow:{row.event_id}:{row.status}"
                rearm_count = int(rearm_counts.get(row.event_id, 0))
                if rearm_count > 0:
                    source_key = f"{source_key}:attempt:{rearm_count}"
                if source_key in delivered:
                    continue
                notifications.append(
                    WorkflowAlertNotification(
                        source_key=source_key,
                        workflow=self._workflow_snapshot(row),
                    )
                )
            return tuple(notifications)

    def expire_pending(self, *, scope: str, timestamp_ns: int) -> tuple[str, ...]:
        """把指定 paper 作用域内到期且未决的审批标记为 EXPIRED。"""
        now = utc_datetime_from_ns(timestamp_ns)
        with Session(self._engine) as session:
            event_ids = tuple(
                session.scalars(
                    select(SignalWorkflowRecord.event_id).where(
                        SignalWorkflowRecord.scope == scope,
                        SignalWorkflowRecord.status == "PENDING",
                        SignalWorkflowRecord.expires_at_utc <= now,
                    )
                )
            )
        return tuple(
            event_id
            for event_id in event_ids
            if self._transition(
                event_id,
                expected_status="PENDING",
                new_status="EXPIRED",
                timestamp_ns=timestamp_ns,
            )
        )

    def list_order_notifications(self, *, scope: str) -> tuple[OrderNotification, ...]:
        """返回指定 paper 作用域内尚未发送的订单终态。"""
        with Session(self._engine) as session:
            delivered = frozenset(session.scalars(select(TelegramDeliveryRecord.source_key)))
            rows = session.scalars(
                select(OrderEventRecord)
                .join(
                    SignalWorkflowRecord,
                    SignalWorkflowRecord.event_id == OrderEventRecord.event_id,
                )
                .where(
                    SignalWorkflowRecord.scope == scope,
                    OrderEventRecord.status.in_(("FILLED", "REJECTED", "DENIED")),
                )
                .order_by(OrderEventRecord.timestamp_utc, OrderEventRecord.id)
            )
            notifications: list[OrderNotification] = []
            for row in rows:
                source_id = row.order_event_id or str(row.id)
                source_key = f"order:{source_id}"
                if source_key in delivered:
                    continue
                notifications.append(
                    OrderNotification(
                        source_key=source_key,
                        event_id=row.event_id,
                        timestamp_utc=self._as_utc(row.timestamp_utc),
                        instrument_id=row.instrument_id,
                        client_order_id=row.client_order_id,
                        status=row.status,
                        direction=row.direction,
                        quantity=row.quantity,
                        reason=row.reason,
                    )
                )
            return tuple(notifications)

    def list_factor_alert_notifications(
        self, *, scope: str
    ) -> tuple[tuple[str, FactorDecisionAudit], ...]:
        """每个交易日及原因只发一次跳过提醒和一次恢复提醒。"""
        with Session(self._engine) as session:
            delivered = set(session.scalars(select(TelegramDeliveryRecord.source_key)))
        result: list[tuple[str, FactorDecisionAudit]] = []
        for decision in self.list_factor_decisions(scope=scope):
            if decision.status != "SKIP":
                continue
            phase = "recovered" if decision.recovered_at is not None else "skip"
            key = f"factor:{decision.id}:{phase}"
            if key not in delivered:
                result.append((key, decision))
        return tuple(result)

    def mark_telegram_delivered(
        self,
        *,
        source_key: str,
        chat_id: str,
        message_id: str,
        timestamp_ns: int,
    ) -> bool:
        """幂等记录一条已发送通知。"""
        try:
            with self._sessions.begin() as session:
                session.add(
                    TelegramDeliveryRecord(
                        source_key=source_key,
                        chat_id=chat_id,
                        message_id=message_id,
                        sent_at=utc_datetime_from_ns(timestamp_ns),
                    )
                )
        except IntegrityError:
            return False
        return True

    def _transition(
        self,
        event_id: str,
        *,
        expected_status: WorkflowStatus,
        new_status: WorkflowStatus,
        timestamp_ns: int,
        extra_conditions: tuple[Any, ...] = (),
        values: dict[str, Any] | None = None,
    ) -> bool:
        update_values = dict(values or {})
        update_values.update(
            status=new_status,
            updated_at=utc_datetime_from_ns(timestamp_ns),
        )
        statement = (
            update(SignalWorkflowRecord)
            .where(
                SignalWorkflowRecord.event_id == event_id,
                SignalWorkflowRecord.status == expected_status,
                *extra_conditions,
            )
            .values(**update_values)
        )
        with self._sessions.begin() as session:
            result = cast("CursorResult[Any]", session.execute(statement))
            return result.rowcount == 1

    @staticmethod
    def _workflow_snapshot(row: SignalWorkflowRecord) -> SignalWorkflow:
        target_weights = tuple((str(item[0]), float(item[1])) for item in row.target_weights)
        return SignalWorkflow(
            event_id=row.event_id,
            scope=row.scope,
            strategy_name=row.strategy_name,
            rebalance_key=row.rebalance_key,
            target_weights=target_weights,
            reason=row.reason,
            signal_timestamp_utc=TradingRepository._as_utc(row.signal_timestamp_utc),
            expires_at_utc=TradingRepository._as_utc(row.expires_at_utc),
            status=row.status,
            planned_orders=tuple(row.planned_orders or ()),
            risk_summary=row.risk_summary,
            telegram_chat_id=row.telegram_chat_id,
            telegram_message_id=row.telegram_message_id,
            preserve_positions=tuple(row.preserve_positions or ()),
            not_before_ns=(
                0
                if row.not_before_utc is None
                else utc_ns_from_datetime(TradingRepository._as_utc(row.not_before_utc))
            ),
            factor_context=(
                None
                if row.factor_context is None
                else FactorContext.from_payload(row.factor_context)
            ),
        )

    @staticmethod
    def _order_audit_snapshot(row: OrderEventRecord) -> OrderAudit:
        return OrderAudit(
            timestamp_utc=TradingRepository._as_utc(row.timestamp_utc),
            event_id=row.event_id,
            instrument_id=row.instrument_id,
            client_order_id=row.client_order_id,
            status=row.status,
            direction=row.direction,
            quantity=row.quantity,
            reason=row.reason,
            venue_order_id=row.venue_order_id,
        )

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def record_approval(
        self,
        event: TradeSignalEvent,
        *,
        approval_mode: str,
        decision: str,
        reason: str,
        timestamp_ns: int,
    ) -> None:
        """记录审批或风控决策。"""
        row = ApprovalRecord(
            event_id=str(event.id),
            timestamp_utc=utc_datetime_from_ns(timestamp_ns),
            strategy_name=event.strategy_name,
            approval_mode=approval_mode,
            decision=decision,
            reason=reason,
        )
        with self._sessions.begin() as session:
            session.add(row)

    def record_order_event(
        self,
        *,
        signal_event_id: str,
        order_event_id: str | None,
        timestamp_ns: int,
        strategy_name: str,
        instrument_id: str,
        client_order_id: str,
        status: str,
        direction: str,
        quantity: float,
        reason: str,
        venue_order_id: str | None = None,
    ) -> None:
        """记录一条订单生命周期事件。"""
        row = OrderEventRecord(
            event_id=signal_event_id,
            order_event_id=order_event_id,
            venue_order_id=venue_order_id,
            timestamp_utc=utc_datetime_from_ns(timestamp_ns),
            strategy_name=strategy_name,
            instrument_id=instrument_id,
            client_order_id=client_order_id,
            status=status,
            direction=direction,
            quantity=quantity,
            reason=reason,
        )
        with self._sessions.begin() as session:
            values = {
                column.name: getattr(row, column.name)
                for column in row.__table__.columns
                if column.name != "id"
            }
            session.execute(
                insert(OrderEventRecord)
                .values(**values)
                .on_conflict_do_nothing(index_elements=[OrderEventRecord.order_event_id])
            )

    def daily_new_position_count(self, *, scope: str, timestamp_ns: int) -> int:
        """从已提交订单与批准计划恢复当日开仓计数, 重放及重启均不清零。"""
        beginning = utc_datetime_from_ns(timestamp_ns).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        with Session(self._engine) as session:
            rows = session.execute(
                select(
                    OrderEventRecord.client_order_id,
                    OrderEventRecord.instrument_id,
                    SignalWorkflowRecord.planned_orders,
                    func.min(OrderEventRecord.timestamp_utc),
                )
                .join(
                    SignalWorkflowRecord, SignalWorkflowRecord.event_id == OrderEventRecord.event_id
                )
                .where(
                    SignalWorkflowRecord.scope == scope,
                    OrderEventRecord.direction == "BUY",
                )
                .group_by(
                    OrderEventRecord.client_order_id,
                    OrderEventRecord.instrument_id,
                    SignalWorkflowRecord.planned_orders,
                )
                .having(
                    func.sum(
                        case(
                            (
                                OrderEventRecord.status.in_(
                                    ("SUBMITTED", "ACCEPTED", "PARTIALLY_FILLED", "FILLED")
                                ),
                                1,
                            ),
                            else_=0,
                        )
                    )
                    > 0
                )
            )
            opened: set[str] = set()
            for client_id, instrument, planned, first_seen in rows:
                # 旧报告今天重放不能把昨日的订单计为今天的新开仓。
                if not beginning <= self._as_utc(first_seen) < beginning + timedelta(days=1):
                    continue
                item = next(
                    (item for item in planned or () if item.get("instrument_id") == instrument),
                    None,
                )
                # 历史计划缺少开仓标记时保守计入, 不以缺字段清零限制。
                if item is None or item.get("opens_position", True):
                    opened.add(client_id)
            return len(opened)

    def record_fill(
        self,
        *,
        run_id: str | None,
        signal_event_id: str,
        trade_id: str,
        timestamp_ns: int,
        strategy_name: str,
        instrument_id: str,
        client_order_id: str,
        direction: str,
        quantity: float,
        price: float,
        commission: float,
        order_event: OrderEventRecord | None = None,
    ) -> bool:
        """原子保存成交及对应订单状态; 一致重放无副作用, 内容冲突报错。"""
        audit_trade_id = f"{run_id}:{trade_id}" if run_id is not None else trade_id
        values = {
            "run_id": run_id,
            "event_id": signal_event_id,
            "trade_id": audit_trade_id,
            "timestamp_utc": utc_datetime_from_ns(timestamp_ns),
            "strategy_name": strategy_name,
            "instrument_id": instrument_id,
            "client_order_id": client_order_id,
            "direction": direction,
            "quantity": quantity,
            "price": price,
            "commission": commission,
        }
        with self._sessions.begin() as session:
            inserted = session.execute(
                insert(FillRecord)
                .values(**values)
                .on_conflict_do_nothing(index_elements=[FillRecord.trade_id])
                .returning(FillRecord.id)
            ).scalar_one_or_none()
            if inserted is None:
                existing = session.scalar(
                    select(FillRecord).where(FillRecord.trade_id == audit_trade_id)
                )
                assert existing is not None
                for key, value in values.items():
                    actual = getattr(existing, key)
                    if isinstance(actual, datetime):
                        actual = self._as_utc(actual)
                    if actual != value:
                        raise ValueError(f"Conflicting fill replay: {audit_trade_id}: {key}")
                return False
            if order_event is not None:
                session.add(order_event)
        return True

    def start_backtest_run(self, run_id: str, started_at: datetime) -> None:
        """记录回测开始。"""
        with self._sessions.begin() as session:
            session.add(
                BacktestRunRecord(
                    run_id=run_id,
                    started_at=started_at,
                    completed_at=None,
                    status="RUNNING",
                    summary=None,
                )
            )

    def complete_backtest_run(
        self,
        run_id: str,
        *,
        completed_at: datetime,
        status: str,
        summary: dict[str, Any],
    ) -> None:
        """写入回测终态和摘要。"""
        with self._sessions.begin() as session:
            row = session.get(BacktestRunRecord, run_id)
            if row is None:
                raise LookupError(f"Backtest run not found: {run_id}")
            row.completed_at = completed_at
            row.status = status
            row.summary = summary

    def list_backtest_runs(
        self,
        *,
        limit: int = 500,
        offset: int = 0,
    ) -> tuple[BacktestRunAudit, ...]:
        """按开始时间倒序返回回测运行。"""
        with Session(self._engine) as session:
            rows = session.scalars(
                select(BacktestRunRecord)
                .order_by(BacktestRunRecord.started_at.desc(), BacktestRunRecord.run_id.desc())
                .offset(offset)
                .limit(limit)
            )
            return tuple(self._backtest_run_snapshot(row) for row in rows)

    def get_backtest_run(self, run_id: str) -> BacktestRunAudit | None:
        """返回指定回测运行。"""
        with Session(self._engine) as session:
            row = session.get(BacktestRunRecord, run_id)
            return None if row is None else self._backtest_run_snapshot(row)

    def list_fills(self, run_id: str) -> tuple[FillAudit, ...]:
        """按时间顺序返回某次回测的成交。"""
        with Session(self._engine) as session:
            rows = session.scalars(
                select(FillRecord)
                .where(FillRecord.run_id == run_id)
                .order_by(FillRecord.timestamp_utc, FillRecord.id)
            )
            return tuple(
                FillAudit(
                    trade_id=row.trade_id,
                    timestamp_utc=row.timestamp_utc.replace(tzinfo=UTC)
                    if row.timestamp_utc.tzinfo is None
                    else row.timestamp_utc.astimezone(UTC),
                    instrument_id=row.instrument_id,
                    client_order_id=row.client_order_id,
                    direction=row.direction,
                    quantity=row.quantity,
                    price=row.price,
                    commission=row.commission,
                    event_id=row.event_id,
                    strategy_name=row.strategy_name,
                )
                for row in rows
            )

    def count(self, model: type[Any]) -> int:
        """返回模型行数。供验收测试使用。"""
        with Session(self._engine) as session:
            return len(list(session.scalars(select(model))))

    @staticmethod
    def _backtest_run_snapshot(row: BacktestRunRecord) -> BacktestRunAudit:
        return BacktestRunAudit(
            run_id=row.run_id,
            started_at=TradingRepository._as_utc(row.started_at),
            completed_at=(
                None if row.completed_at is None else TradingRepository._as_utc(row.completed_at)
            ),
            status=row.status,
            summary=None if row.summary is None else dict(row.summary),
        )

"""SQLite/SQLAlchemy 审计仓储。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

from nautilus_trader.core.uuid import UUID4
from sqlalchemy import create_engine, func, select, update
from sqlalchemy.engine import CursorResult, Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from trading_assistant.execution.events import TradeSignalEvent
from trading_assistant.storage.models import (
    AccountSnapshotRecord,
    ApprovalRecord,
    BacktestRunRecord,
    Base,
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


def utc_datetime_from_ns(timestamp_ns: int) -> datetime:
    """把 Unix 纳秒转换为 UTC datetime。"""
    return datetime.fromtimestamp(timestamp_ns / 1_000_000_000, tz=UTC)


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

    def to_event(self) -> TradeSignalEvent:
        """恢复 Gateway 可消费的不可变 NT 领域事件。"""
        ts_event = int(self.signal_timestamp_utc.timestamp() * 1_000_000_000)
        return TradeSignalEvent(
            strategy_name=self.strategy_name,
            target_weights=self.target_weights,
            rebalance_key=self.rebalance_key,
            reason=self.reason,
            expires_at_ns=int(self.expires_at_utc.timestamp() * 1_000_000_000),
            ts_event=ts_event,
            ts_init=ts_event,
            event_id=UUID4.from_str(self.event_id),
        )


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
    free_cash: float
    locked_cash: float
    positions: tuple[PositionSnapshot, ...]


@dataclass(frozen=True)
class AccountSnapshotAudit:
    """脱离 Session 的只读账户资金快照。"""

    timestamp_utc: datetime
    account_id: str
    currency: str
    net_liquidation: float
    free_cash: float
    locked_cash: float


@dataclass(frozen=True)
class SignalReview:
    """Dashboard 使用的信号与工作流联合视图。"""

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


class TradingRepository:
    """记录信号、审批、订单、成交和回测运行。"""

    def __init__(self, database_url: str, *, read_only: bool = False) -> None:
        if not read_only:
            self._ensure_sqlite_parent(database_url)
        connect_args: dict[str, Any] = {}
        if database_url.startswith("sqlite:"):
            connect_args["timeout"] = 30
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
        Base.metadata.create_all(self._engine)

    def close(self) -> None:
        """释放数据库连接池。"""
        self._engine.dispose()

    def record_portfolio_snapshot(
        self,
        *,
        timestamp_ns: int,
        account_id: str,
        currency: str,
        net_liquidation: float,
        free_cash: float,
        locked_cash: float,
        positions: tuple[PositionSnapshotInput, ...],
    ) -> None:
        """原子记录一次 NT 账户资金与全部开仓仓位。"""
        with self._sessions.begin() as session:
            account = AccountSnapshotRecord(
                timestamp_utc=utc_datetime_from_ns(timestamp_ns),
                account_id=account_id,
                currency=currency,
                net_liquidation=net_liquidation,
                free_cash=free_cash,
                locked_cash=locked_cash,
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
                free_cash=account.free_cash,
                locked_cash=account.locked_cash,
                positions=positions,
            )

    def list_account_snapshots(
        self,
        *,
        account_id: str,
        limit: int = 500,
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
                .limit(limit)
            )
            return tuple(
                AccountSnapshotAudit(
                    timestamp_utc=self._as_utc(row.timestamp_utc),
                    account_id=row.account_id,
                    currency=row.currency,
                    net_liquidation=row.net_liquidation,
                    free_cash=row.free_cash,
                    locked_cash=row.locked_cash,
                )
                for row in rows
            )

    def list_signal_workflows(
        self,
        *,
        scope: str,
        limit: int = 500,
    ) -> tuple[SignalWorkflow, ...]:
        """按更新时间倒序读取指定作用域的工作流。"""
        with Session(self._engine) as session:
            rows = session.scalars(
                select(SignalWorkflowRecord)
                .where(SignalWorkflowRecord.scope == scope)
                .order_by(
                    SignalWorkflowRecord.updated_at.desc(),
                    SignalWorkflowRecord.event_id.desc(),
                )
                .limit(limit)
            )
            return tuple(self._workflow_snapshot(row) for row in rows)

    def list_signal_reviews(self, *, scope: str, limit: int = 500) -> tuple[SignalReview, ...]:
        """按时间倒序读取指定作用域的逐标的信号。"""
        with Session(self._engine) as session:
            rows = session.execute(
                select(SignalRecord, SignalWorkflowRecord)
                .join(
                    SignalWorkflowRecord,
                    SignalWorkflowRecord.event_id == SignalRecord.event_id,
                )
                .where(SignalWorkflowRecord.scope == scope)
                .order_by(SignalRecord.timestamp_utc.desc(), SignalRecord.id.desc())
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

    def list_decision_audits(self, *, scope: str, limit: int = 500) -> tuple[DecisionAudit, ...]:
        """按时间倒序读取指定作用域的风控与审批事件。"""
        with Session(self._engine) as session:
            rows = session.scalars(
                select(ApprovalRecord)
                .join(
                    SignalWorkflowRecord,
                    SignalWorkflowRecord.event_id == ApprovalRecord.event_id,
                )
                .where(SignalWorkflowRecord.scope == scope)
                .order_by(ApprovalRecord.timestamp_utc.desc(), ApprovalRecord.id.desc())
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

    def list_order_audits(self, *, scope: str, limit: int = 500) -> tuple[OrderAudit, ...]:
        """按时间倒序读取指定作用域的订单生命周期。"""
        with Session(self._engine) as session:
            rows = session.scalars(
                select(OrderEventRecord)
                .join(
                    SignalWorkflowRecord,
                    SignalWorkflowRecord.event_id == OrderEventRecord.event_id,
                )
                .where(SignalWorkflowRecord.scope == scope)
                .order_by(OrderEventRecord.timestamp_utc.desc(), OrderEventRecord.id.desc())
                .limit(limit)
            )
            return tuple(
                OrderAudit(
                    timestamp_utc=self._as_utc(row.timestamp_utc),
                    event_id=row.event_id,
                    instrument_id=row.instrument_id,
                    client_order_id=row.client_order_id,
                    status=row.status,
                    direction=row.direction,
                    quantity=row.quantity,
                    reason=row.reason,
                )
                for row in rows
            )

    def list_fill_audits(self, *, scope: str, limit: int = 500) -> tuple[FillAudit, ...]:
        """按时间倒序读取指定作用域的 paper/live 成交。"""
        with Session(self._engine) as session:
            rows = session.scalars(
                select(FillRecord)
                .join(
                    SignalWorkflowRecord,
                    SignalWorkflowRecord.event_id == FillRecord.event_id,
                )
                .where(
                    SignalWorkflowRecord.scope == scope,
                    FillRecord.run_id.is_(None),
                )
                .order_by(FillRecord.timestamp_utc.desc(), FillRecord.id.desc())
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

    def claim_auto_signal(self, event_id: str, *, timestamp_ns: int) -> bool:
        """由 Gateway 独占一个 auto 信号。"""
        return self._transition(
            event_id,
            expected_status="NEW",
            new_status="PROCESSING",
            timestamp_ns=timestamp_ns,
        )

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

    def claim_next_approved(self, *, timestamp_ns: int) -> SignalWorkflow | None:
        """由 Gateway 原子领取最早获批的人工信号。"""
        with Session(self._engine) as session:
            event_ids = tuple(
                session.scalars(
                    select(SignalWorkflowRecord.event_id)
                    .where(SignalWorkflowRecord.status == "APPROVED")
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
    ) -> None:
        """记录一条订单生命周期事件。"""
        row = OrderEventRecord(
            event_id=signal_event_id,
            order_event_id=order_event_id,
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
            session.add(row)

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
    ) -> None:
        """记录逐笔成交。"""
        audit_trade_id = f"{run_id}:{trade_id}" if run_id is not None else trade_id
        row = FillRecord(
            run_id=run_id,
            event_id=signal_event_id,
            trade_id=audit_trade_id,
            timestamp_utc=utc_datetime_from_ns(timestamp_ns),
            strategy_name=strategy_name,
            instrument_id=instrument_id,
            client_order_id=client_order_id,
            direction=direction,
            quantity=quantity,
            price=price,
            commission=commission,
        )
        with self._sessions.begin() as session:
            session.add(row)

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

"""信号、工作流、订单和成交的只读查询服务。"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime

from sqlalchemy.exc import SQLAlchemyError

from trading_assistant.application.models import (
    DecisionView,
    FactorDecisionView,
    FillView,
    OrderDetailView,
    OrderEventView,
    OrderSummaryView,
    Page,
    QuerySourceError,
    ResourceNotFoundError,
    Scalar,
    SignalView,
    TimelineEventView,
    WorkflowDetailView,
    WorkflowView,
)
from trading_assistant.storage.repository import (
    ORDER_STATUS_PRIORITY,
    FillAudit,
    OrderAudit,
    SignalWorkflow,
    TradingRepository,
)

_AUDIT_SCAN_LIMIT = 5_000


def _safe_mapping(value: dict[str, object]) -> dict[str, Scalar]:
    return {
        key: item
        for key, item in value.items()
        if isinstance(item, (str, int, float, bool)) or item is None
    }


def _workflow_view(workflow: SignalWorkflow) -> WorkflowView:
    return WorkflowView(
        event_id=workflow.event_id,
        strategy_name=workflow.strategy_name,
        rebalance_key=workflow.rebalance_key,
        signal_timestamp_utc=workflow.signal_timestamp_utc,
        expires_at_utc=workflow.expires_at_utc,
        status=workflow.status,
        reason=workflow.reason,
        target_count=len(workflow.target_weights),
        planned_order_count=len(workflow.planned_orders),
        risk_summary=workflow.risk_summary,
    )


def _order_event_view(row: OrderAudit) -> OrderEventView:
    return OrderEventView(
        timestamp_utc=row.timestamp_utc,
        event_id=row.event_id,
        instrument_id=row.instrument_id,
        client_order_id=row.client_order_id,
        status=row.status,
        direction=row.direction,
        quantity=row.quantity,
        reason=row.reason,
    )


def _fill_view(row: FillAudit) -> FillView:
    return FillView(
        trade_id=row.trade_id,
        timestamp_utc=row.timestamp_utc,
        event_id=row.event_id,
        strategy_name=row.strategy_name,
        instrument_id=row.instrument_id,
        client_order_id=row.client_order_id,
        direction=row.direction,
        quantity=row.quantity,
        price=row.price,
        commission=row.commission,
    )


class TradingActivityQueryService:
    """从 live 审计库构造交易活动视图。"""

    def __init__(
        self,
        *,
        repository: TradingRepository | None,
        scope: str | None,
    ) -> None:
        self._repository = repository
        self._scope = scope

    def list_signals(
        self,
        *,
        offset: int,
        limit: int,
        status: str | None,
        instrument_id: str | None,
    ) -> Page[SignalView]:
        if self._repository is None or self._scope is None:
            return Page(items=(), offset=offset, limit=limit, has_more=False)
        try:
            rows = self._repository.list_signal_reviews(
                scope=self._scope,
                limit=limit + 1,
                offset=offset,
                status=status,
                instrument_id=instrument_id,
            )
        except SQLAlchemyError as exc:
            raise QuerySourceError("live_database", "交易审计库无法读取") from exc
        items = tuple(
            SignalView(
                event_id=row.event_id,
                timestamp_utc=row.timestamp_utc,
                strategy_name=row.strategy_name,
                instrument_id=row.instrument_id,
                direction=row.direction,
                target_weight=row.target_weight,
                reason=row.reason,
                status=row.status,
            )
            for row in rows[:limit]
        )
        return Page(items=items, offset=offset, limit=limit, has_more=len(rows) > limit)

    def list_workflows(
        self,
        *,
        offset: int,
        limit: int,
        status: str | None,
    ) -> Page[WorkflowView]:
        if self._repository is None or self._scope is None:
            return Page(items=(), offset=offset, limit=limit, has_more=False)
        try:
            rows = self._repository.list_signal_workflows(
                scope=self._scope,
                limit=limit + 1,
                offset=offset,
                status=status,
            )
        except SQLAlchemyError as exc:
            raise QuerySourceError("live_database", "交易审计库无法读取") from exc
        return Page(
            items=tuple(_workflow_view(row) for row in rows[:limit]),
            offset=offset,
            limit=limit,
            has_more=len(rows) > limit,
        )

    def list_factor_decisions(self) -> tuple[FactorDecisionView, ...]:
        """在现有活动查询中展示跳过与恢复, 不伪造工作流。"""
        if self._repository is None or self._scope is None:
            return ()
        try:
            rows = self._repository.list_factor_decisions(scope=self._scope, limit=50)
        except SQLAlchemyError as exc:
            raise QuerySourceError("live_database", "因子审计库无法读取") from exc
        return tuple(
            FactorDecisionView(
                row.id,
                row.asof_date,
                row.status,
                row.reason,
                row.preserve_positions,
                None if row.context is None else len(row.context.candidate_ids),
                None if row.context is None else row.context.eligible_count,
                None if row.context is None else row.context.model_release_id,
                None if row.context is None else row.context.delivery_id,
                row.last_seen,
                row.recovered_at,
            )
            for row in rows
        )

    def workflow_detail(self, event_id: str) -> WorkflowDetailView:
        repository, scope = self._required_source()
        try:
            workflow = repository.get_signal_workflow(event_id)
            if workflow is None or workflow.scope != scope:
                raise ResourceNotFoundError("工作流不存在")
            decisions = repository.list_decision_audits(
                scope=scope,
                event_id=event_id,
                limit=_AUDIT_SCAN_LIMIT,
            )
            orders = repository.list_order_audits(
                scope=scope,
                event_id=event_id,
                limit=_AUDIT_SCAN_LIMIT,
            )
            fills = repository.list_fill_audits(
                scope=scope,
                event_id=event_id,
                limit=_AUDIT_SCAN_LIMIT,
            )
        except SQLAlchemyError as exc:
            raise QuerySourceError("live_database", "交易审计库无法读取") from exc

        decision_views = tuple(
            DecisionView(
                timestamp_utc=row.timestamp_utc,
                event_id=row.event_id,
                strategy_name=row.strategy_name,
                approval_mode=row.approval_mode,
                decision=row.decision,
                reason=row.reason,
            )
            for row in reversed(decisions)
        )
        order_views = tuple(_order_event_view(row) for row in reversed(orders))
        fill_views = tuple(_fill_view(row) for row in reversed(fills))
        timeline: list[TimelineEventView] = [
            TimelineEventView(
                timestamp_utc=workflow.signal_timestamp_utc,
                kind="signal",
                status=workflow.status,
                title="Signal created",
                detail=workflow.reason,
            )
        ]
        timeline.extend(
            TimelineEventView(
                timestamp_utc=row.timestamp_utc,
                kind="decision",
                status=row.decision,
                title=f"{row.approval_mode} decision",
                detail=row.reason,
            )
            for row in decision_views
        )
        timeline.extend(
            TimelineEventView(
                timestamp_utc=row.timestamp_utc,
                kind="order",
                status=row.status,
                title=f"{row.direction} {row.instrument_id}",
                detail=row.reason,
            )
            for row in order_views
        )
        timeline.extend(
            TimelineEventView(
                timestamp_utc=row.timestamp_utc,
                kind="fill",
                status="FILLED",
                title=f"{row.direction} {row.instrument_id}",
                detail=f"{row.quantity:g} @ {row.price:g}",
            )
            for row in fill_views
        )
        timeline.sort(key=lambda item: (item.timestamp_utc, item.kind, item.title))
        return WorkflowDetailView(
            workflow=_workflow_view(workflow),
            target_weights=workflow.target_weights,
            planned_orders=tuple(_safe_mapping(item) for item in workflow.planned_orders),
            decisions=decision_views,
            orders=order_views,
            fills=fill_views,
            timeline=tuple(timeline),
            preserve_positions=workflow.preserve_positions,
            not_before_utc=(
                None
                if not workflow.not_before_ns
                else datetime.fromtimestamp(workflow.not_before_ns / 1e9, tz=UTC)
            ),
            factor_asof_date=(
                None if workflow.factor_context is None else workflow.factor_context.asof_date
            ),
            model_release_id=(
                None
                if workflow.factor_context is None
                else workflow.factor_context.model_release_id
            ),
            delivery_id=(
                None if workflow.factor_context is None else workflow.factor_context.delivery_id
            ),
        )

    def list_orders(
        self,
        *,
        offset: int,
        limit: int,
        status: str | None,
        instrument_id: str | None,
    ) -> Page[OrderSummaryView]:
        repository, scope = self._optional_source()
        if repository is None or scope is None:
            return Page(items=(), offset=offset, limit=limit, has_more=False)
        try:
            latest = repository.list_latest_order_audits(
                scope=scope,
                limit=limit + 1,
                offset=offset,
                status=status,
                instrument_id=instrument_id,
            )
            page_latest = latest[:limit]
            client_order_ids = tuple(item.client_order_id for item in page_latest)
            events = (
                ()
                if not client_order_ids
                else repository.list_order_audits(
                    scope=scope,
                    client_order_ids=client_order_ids,
                    limit=_AUDIT_SCAN_LIMIT,
                )
            )
            fills = (
                ()
                if not client_order_ids
                else repository.list_fill_audits(
                    scope=scope,
                    client_order_ids=client_order_ids,
                    limit=_AUDIT_SCAN_LIMIT,
                )
            )
        except SQLAlchemyError as exc:
            raise QuerySourceError("live_database", "交易审计库无法读取") from exc
        summaries = self._order_summaries(events, fills)
        by_id = {item.client_order_id: item for item in summaries}
        return Page(
            items=tuple(by_id[item.client_order_id] for item in page_latest),
            offset=offset,
            limit=limit,
            has_more=len(latest) > limit,
        )

    def order_detail(self, client_order_id: str) -> OrderDetailView:
        repository, scope = self._required_source()
        try:
            events = repository.list_order_audits(
                scope=scope,
                client_order_id=client_order_id,
                limit=_AUDIT_SCAN_LIMIT,
            )
            fills = repository.list_fill_audits(
                scope=scope,
                client_order_id=client_order_id,
                limit=_AUDIT_SCAN_LIMIT,
            )
        except SQLAlchemyError as exc:
            raise QuerySourceError("live_database", "交易审计库无法读取") from exc
        if not events:
            raise ResourceNotFoundError("订单不存在")
        summaries = self._order_summaries(events, fills)
        return OrderDetailView(
            summary=summaries[0],
            events=tuple(_order_event_view(row) for row in reversed(events)),
            fills=tuple(_fill_view(row) for row in reversed(fills)),
        )

    def list_fills(
        self,
        *,
        offset: int,
        limit: int,
        instrument_id: str | None,
    ) -> Page[FillView]:
        repository, scope = self._optional_source()
        if repository is None or scope is None:
            return Page(items=(), offset=offset, limit=limit, has_more=False)
        try:
            rows = repository.list_fill_audits(
                scope=scope,
                instrument_id=instrument_id,
                limit=limit + 1,
                offset=offset,
            )
        except SQLAlchemyError as exc:
            raise QuerySourceError("live_database", "交易审计库无法读取") from exc
        return Page(
            items=tuple(_fill_view(row) for row in rows[:limit]),
            offset=offset,
            limit=limit,
            has_more=len(rows) > limit,
        )

    def overview_activity(
        self,
    ) -> tuple[dict[str, int], datetime | None, datetime | None]:
        """返回首屏所需的状态计数及最近工作流/成交时间。"""
        repository, scope = self._optional_source()
        if repository is None or scope is None:
            return {}, None, None
        try:
            counts = repository.workflow_status_counts(scope=scope)
            workflows = repository.list_signal_workflows(scope=scope, limit=1)
            fills = repository.list_fill_audits(scope=scope, limit=1)
        except SQLAlchemyError as exc:
            raise QuerySourceError("live_database", "交易审计库无法读取") from exc
        latest_workflow = workflows[0].signal_timestamp_utc if workflows else None
        latest_fill = fills[0].timestamp_utc if fills else None
        return counts, latest_workflow, latest_fill

    @staticmethod
    def _order_summaries(
        events: tuple[OrderAudit, ...],
        fills: tuple[FillAudit, ...],
    ) -> tuple[OrderSummaryView, ...]:
        grouped: dict[str, list[OrderAudit]] = defaultdict(list)
        fills_by_order: dict[str, list[FillAudit]] = defaultdict(list)
        for event in events:
            grouped[event.client_order_id].append(event)
        for fill in fills:
            fills_by_order[fill.client_order_id].append(fill)
        summaries: list[OrderSummaryView] = []
        for client_order_id, values in grouped.items():
            ordered = sorted(values, key=lambda item: item.timestamp_utc)
            latest = max(
                values,
                key=lambda item: (ORDER_STATUS_PRIORITY.get(item.status, 0), item.timestamp_utc),
            )
            order_fills = fills_by_order.get(client_order_id, [])
            summaries.append(
                OrderSummaryView(
                    client_order_id=client_order_id,
                    event_id=latest.event_id,
                    instrument_id=latest.instrument_id,
                    direction=latest.direction,
                    quantity=latest.quantity,
                    status=latest.status,
                    first_event_at_utc=ordered[0].timestamp_utc,
                    latest_event_at_utc=latest.timestamp_utc,
                    event_count=len(ordered),
                    fill_count=len(order_fills),
                    filled_quantity=sum(fill.quantity for fill in order_fills),
                )
            )
        summaries.sort(
            key=lambda item: (item.latest_event_at_utc, item.client_order_id),
            reverse=True,
        )
        return tuple(summaries)

    def _required_source(self) -> tuple[TradingRepository, str]:
        repository, scope = self._optional_source()
        if repository is None or scope is None:
            raise ResourceNotFoundError("交易审计数据不可用")
        return repository, scope

    def _optional_source(self) -> tuple[TradingRepository | None, str | None]:
        return self._repository, self._scope

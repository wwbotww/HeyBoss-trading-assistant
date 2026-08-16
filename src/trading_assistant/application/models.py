"""与传输协议无关的只读查询模型。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

Scalar = str | int | float | bool | None
SourceState = Literal[
    "available",
    "empty",
    "missing",
    "invalid",
    "unconfigured",
    "unobserved",
]


class QuerySourceError(RuntimeError):
    """数据源存在但无法安全读取。"""

    def __init__(self, source: str, detail: str) -> None:
        super().__init__(detail)
        self.source = source
        self.public_detail = detail


class ResourceNotFoundError(LookupError):
    """查询对象不存在。"""


@dataclass(frozen=True)
class Page[T]:
    """不依赖 HTTP 的偏移分页结果。"""

    items: tuple[T, ...]
    offset: int
    limit: int
    has_more: bool


@dataclass(frozen=True)
class SourceStatus:
    """一个只读数据源的可观测状态。"""

    name: str
    state: SourceState
    observed_at_utc: datetime
    last_event_at_utc: datetime | None
    detail: str


@dataclass(frozen=True)
class PositionView:
    """带可选 EOD 参考估值的持仓。"""

    canonical_id: str
    source_instrument_id: str
    symbol: str
    side: str
    signed_quantity: float
    avg_open_price: float
    realized_pnl: float | None
    reference_price: float | None
    reference_price_at_utc: datetime | None
    estimated_market_value: float | None
    estimated_weight: float | None
    price_kind: str | None


@dataclass(frozen=True)
class PortfolioView:
    """账户最近一次完整资金和持仓快照。"""

    source_state: SourceState
    observed_at_utc: datetime
    snapshot_at_utc: datetime | None
    account_id: str | None
    currency: str | None
    net_liquidation: float | None
    free_cash: float | None
    locked_cash: float | None
    age_seconds: float | None
    is_stale: bool | None
    positions: tuple[PositionView, ...]


@dataclass(frozen=True)
class AccountHistoryPoint:
    """账户资金历史中的一个采样点。"""

    timestamp_utc: datetime
    account_id: str
    currency: str
    net_liquidation: float
    free_cash: float
    locked_cash: float


@dataclass(frozen=True)
class StrategyView:
    """当前活动策略与统一风控配置。"""

    source_state: SourceState
    observed_at_utc: datetime
    name: str | None
    approval_mode: str | None
    signal_expiry_hours: int | None
    parameters: dict[str, Scalar]
    risk_limits: dict[str, Scalar]


@dataclass(frozen=True)
class FactorScoreView:
    """最新完整因子横截面中的一行。"""

    canonical_id: str
    symbol: str
    security_id: str
    score: float
    eligible: bool
    rank: int | None
    selected: bool
    target_weight: float


@dataclass(frozen=True)
class FactorSnapshotView:
    """Catalog 中最新的完整因子批次。"""

    source_state: SourceState
    observed_at_utc: datetime
    asof_date: str | None
    available_at_utc: datetime | None
    batch_id: str | None
    delivery_id: str | None
    model_release_id: str | None
    source_kind: str | None
    expected_rows: int
    scores: tuple[FactorScoreView, ...]


@dataclass(frozen=True)
class SignalView:
    """按标的展开的交易信号。"""

    event_id: str
    timestamp_utc: datetime
    strategy_name: str
    instrument_id: str
    direction: str
    target_weight: float
    reason: str
    status: str


@dataclass(frozen=True)
class WorkflowView:
    """交易工作流摘要。"""

    event_id: str
    strategy_name: str
    rebalance_key: str
    signal_timestamp_utc: datetime
    expires_at_utc: datetime
    status: str
    reason: str
    target_count: int
    planned_order_count: int
    risk_summary: str | None


@dataclass(frozen=True)
class DecisionView:
    """风控或审批事件。"""

    timestamp_utc: datetime
    event_id: str
    strategy_name: str
    approval_mode: str
    decision: str
    reason: str


@dataclass(frozen=True)
class OrderEventView:
    """单个订单生命周期事件。"""

    timestamp_utc: datetime
    event_id: str
    instrument_id: str
    client_order_id: str
    status: str
    direction: str
    quantity: float
    reason: str


@dataclass(frozen=True)
class FillView:
    """逐笔成交。"""

    trade_id: str
    timestamp_utc: datetime
    event_id: str
    strategy_name: str
    instrument_id: str
    client_order_id: str
    direction: str
    quantity: float
    price: float
    commission: float


@dataclass(frozen=True)
class TimelineEventView:
    """工作流详情中统一排序的审计节点。"""

    timestamp_utc: datetime
    kind: Literal["signal", "decision", "order", "fill"]
    status: str
    title: str
    detail: str


@dataclass(frozen=True)
class WorkflowDetailView:
    """信号到成交的完整只读工作流。"""

    workflow: WorkflowView
    target_weights: tuple[tuple[str, float], ...]
    planned_orders: tuple[dict[str, Scalar], ...]
    decisions: tuple[DecisionView, ...]
    orders: tuple[OrderEventView, ...]
    fills: tuple[FillView, ...]
    timeline: tuple[TimelineEventView, ...]


@dataclass(frozen=True)
class OrderSummaryView:
    """按 client_order_id 聚合的订单摘要。"""

    client_order_id: str
    event_id: str
    instrument_id: str
    direction: str
    quantity: float
    status: str
    first_event_at_utc: datetime
    latest_event_at_utc: datetime
    event_count: int
    fill_count: int
    filled_quantity: float


@dataclass(frozen=True)
class OrderDetailView:
    """一个订单的生命周期和成交。"""

    summary: OrderSummaryView
    events: tuple[OrderEventView, ...]
    fills: tuple[FillView, ...]


@dataclass(frozen=True)
class BacktestRunView:
    """回测运行及报告可用状态。"""

    run_id: str
    started_at_utc: datetime | None
    completed_at_utc: datetime | None
    status: str
    strategy_name: str | None
    evaluation_start: str | None
    end: str | None
    final_equity_usd: float | None
    annualized_return: float | None
    max_drawdown: float | None
    sharpe_ratio: float | None
    report_state: SourceState


@dataclass(frozen=True)
class BacktestDetailView:
    """回测摘要的安全投影。"""

    run: BacktestRunView
    summary: dict[str, Scalar]
    available_tables: tuple[str, ...]


@dataclass(frozen=True)
class ReportTableView:
    """回测 CSV 的通用只读表格。"""

    columns: tuple[str, ...]
    rows: tuple[dict[str, Scalar], ...]
    offset: int
    limit: int
    has_more: bool


@dataclass(frozen=True)
class CatalogCoverageView:
    """一个标的一类日线的 Catalog 覆盖范围。"""

    instrument_id: str
    symbol: str
    price_kind: Literal["signal", "execution"]
    bar_type: str
    state: SourceState
    first_at_utc: datetime | None
    last_at_utc: datetime | None


@dataclass(frozen=True)
class CatalogView:
    """配置股票池的 Catalog 覆盖情况。"""

    source_state: SourceState
    observed_at_utc: datetime
    provider: str
    coverage: tuple[CatalogCoverageView, ...]


@dataclass(frozen=True)
class QualityIssueView:
    """数据质量报告中的一条问题。"""

    code: str
    severity: str
    instrument_id: str
    timestamp_utc: datetime | None
    message: str


@dataclass(frozen=True)
class QualityInstrumentView:
    """数据质量报告中的单标的摘要。"""

    instrument_id: str
    bar_count: int
    first_at_utc: datetime | None
    last_at_utc: datetime | None
    issue_count: int


@dataclass(frozen=True)
class DataQualityView:
    """最近一次历史数据质量检查。"""

    source_state: SourceState
    observed_at_utc: datetime
    generated_at_utc: datetime | None
    mode: str | None
    bars_fetched: int
    bars_written: int
    corporate_actions_written: int
    error_count: int
    warning_count: int
    issues: tuple[QualityIssueView, ...]
    instruments: tuple[QualityInstrumentView, ...]


@dataclass(frozen=True)
class SystemStatusView:
    """Web 能够直接证明的数据源状态。"""

    observed_at_utc: datetime
    sources: tuple[SourceStatus, ...]


@dataclass(frozen=True)
class OverviewView:
    """首屏需要的一组稳定汇总。"""

    observed_at_utc: datetime
    portfolio: PortfolioView
    active_strategy: StrategyView
    latest_factor: FactorSnapshotView
    workflow_status_counts: dict[str, int]
    latest_workflow_at_utc: datetime | None
    latest_fill_at_utc: datetime | None
    data_quality_state: SourceState

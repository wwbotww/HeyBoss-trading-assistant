"""与传输协议无关的只读查询模型。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
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
RadarMetricValidity = Literal["complete", "insufficient_history", "unavailable"]
BreadthMetricValidity = Literal["complete", "partial", "insufficient_coverage"]
MarketBreadthValidity = Literal[
    "complete",
    "partial",
    "stale",
    "insufficient_coverage",
    "unavailable",
]
MacroRegimeValidity = Literal[
    "complete",
    "insufficient_history",
    "stale",
    "unavailable",
]
EarningsRevisionValidity = Literal["complete", "partial", "unavailable"]
MarketEarningsValidity = Literal["complete", "partial", "stale", "unavailable"]
MacroRegimeCode = Literal[
    "transition",
    "easing_risk_on",
    "growth_reflation",
    "growth_concern",
    "tightening_shock",
]
RadarModuleState = Literal[
    "complete",
    "partial",
    "stale",
    "insufficient_history",
    "insufficient_coverage",
    "unavailable",
]
StockRadarSort = Literal[
    "instrument",
    "momentum",
    "relative_momentum",
    "volatility",
    "drawdown",
]
SortDirection = Literal["asc", "desc"]


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
class RadarMetricView:
    """价格指标值及其所需历史边界。"""

    value: float | None
    validity: RadarMetricValidity
    observations: int
    required: int


@dataclass(frozen=True)
class RadarCoverageView:
    """市场雷达指标的真实覆盖率。"""

    eligible: int
    observed: int
    ratio: float


@dataclass(frozen=True)
class RadarFreshnessView:
    """当前成员代理相对查询时点的新鲜度。"""

    membership_age_days: int
    stale_after_days: int


@dataclass(frozen=True)
class BreadthMetricView:
    """当前宽度原始值、历史要求与真实成员覆盖。"""

    value: float | None
    validity: BreadthMetricValidity
    coverage: RadarCoverageView
    history_required: int


@dataclass(frozen=True)
class MarketBreadthView:
    """一个已发布当前成员代理宽度快照的只读投影。"""

    source_state: SourceState
    observed_at_utc: datetime
    validity: MarketBreadthValidity
    as_of_date: date | None
    calculated_at_utc: datetime | None
    membership_date: date | None
    membership_source: str | None
    freshness: RadarFreshnessView | None
    b50: BreadthMetricView | None
    b200: BreadthMetricView | None
    ad10: BreadthMetricView | None
    nhnl: BreadthMetricView | None


@dataclass(frozen=True)
class MacroFreshnessView:
    """宏观双轴相对查询时点的新鲜度。"""

    risk_appetite_age_days: int
    real_rate_age_days: int
    stale_after_days: int


@dataclass(frozen=True)
class MacroRealRateView:
    """FRED 实际利率状态的只读投影。"""

    series_id: str
    observation_date: date
    level_percent: float
    change_20_percentage_points: float | None
    pressure_z: float | None
    percentile_3y: float | None
    validity: RadarMetricValidity
    observations: int
    required: int


@dataclass(frozen=True)
class MacroRiskAppetiteView:
    """信用与波动率组合轴的只读投影。"""

    as_of_date: date
    score: float | None
    credit_z: float | None
    volatility_z: float | None
    validity: RadarMetricValidity
    observations: int
    required: int


@dataclass(frozen=True)
class MacroRegimePointView:
    """一个已在后端完成分类的宏观象限点。"""

    day: date
    real_rate_observation_date: date
    real_rate_level_percent: float
    real_rate_change_20_percentage_points: float
    real_rate_pressure_z: float
    real_rate_percentile_3y: float
    risk_appetite_score: float
    credit_z: float
    volatility_z: float
    regime: MacroRegimeCode
    regime_label: str


@dataclass(frozen=True)
class MacroRegimeView:
    """宏观双轴、象限轨迹和来源边界的只读投影。"""

    source_state: SourceState
    observed_at_utc: datetime
    validity: MacroRegimeValidity
    as_of_date: date | None
    calculated_at_utc: datetime | None
    freshness: MacroFreshnessView | None
    neutral_band: float | None
    alignment_max_age_days: int | None
    real_rate_source: str | None
    real_rate_vintage: str | None
    credit_source: str | None
    price_source: str | None
    real_rate: MacroRealRateView | None
    risk_appetite: MacroRiskAppetiteView | None
    current: MacroRegimePointView | None
    trajectory: tuple[MacroRegimePointView, ...]
    duration_observations: int


@dataclass(frozen=True)
class EarningsFreshnessView:
    """盈利修正快照相对查询时点的新鲜度。"""

    snapshot_age_days: int
    stale_after_days: int


@dataclass(frozen=True)
class EarningsRevisionAggregateView:
    """一个标的集合的盈利修正覆盖和聚合投影。"""

    validity: EarningsRevisionValidity
    eligible: int
    observed: int
    coverage_ratio: float
    upward: int
    downward: int
    unchanged: int
    breadth: float | None
    magnitude_observed: int
    non_positive_or_near_zero: int
    median_magnitude: float | None


@dataclass(frozen=True)
class EarningsMembershipView:
    """市场成员与行业分类联接质量投影。"""

    membership_source: str
    membership_date: date
    member_count: int
    classification_source: str
    classification_record_count: int
    classified_member_count: int
    unclassified_member_count: int
    unused_classification_count: int
    classification_validity: EarningsRevisionValidity
    classification_coverage_ratio: float


@dataclass(frozen=True)
class SectorEarningsRevisionView:
    """单个标准板块的盈利修正聚合。"""

    sector_id: str
    revisions: EarningsRevisionAggregateView


@dataclass(frozen=True)
class MarketEarningsView:
    """最近完整运行发布的统一盈利修正快照。"""

    source_state: SourceState
    observed_at_utc: datetime
    validity: MarketEarningsValidity
    as_of_date: date | None
    calculated_at_utc: datetime | None
    source: str | None
    freshness: EarningsFreshnessView | None
    membership: EarningsMembershipView | None
    watchlist: EarningsRevisionAggregateView | None
    market: EarningsRevisionAggregateView | None
    sectors: tuple[SectorEarningsRevisionView, ...]


@dataclass(frozen=True)
class RadarMarketView:
    """市场价格型摘要。"""

    spy_return_20: RadarMetricView
    spy_distance_ma_200: RadarMetricView
    rsp_spy_return_20: RadarMetricView


@dataclass(frozen=True)
class RadarModuleView:
    """雷达总览中的一个能力状态。"""

    module_id: str
    label: str
    state: RadarModuleState
    detail: str


@dataclass(frozen=True)
class MarketRadarSummaryView:
    """最新完整市场雷达价格快照摘要。"""

    source_state: SourceState
    observed_at_utc: datetime
    as_of_date: date | None
    calculated_at_utc: datetime | None
    coverage: RadarCoverageView | None
    market: RadarMarketView | None
    modules: tuple[RadarModuleView, ...]


@dataclass(frozen=True)
class SectorRadarView:
    """单个板块的价格相对强弱。"""

    sector_id: str
    instrument_id: str
    relative_strength_20: RadarMetricView
    relative_strength_60: RadarMetricView


@dataclass(frozen=True)
class SectorRadarListView:
    """最新完整快照中的板块集合。"""

    source_state: SourceState
    observed_at_utc: datetime
    as_of_date: date | None
    calculated_at_utc: datetime | None
    items: tuple[SectorRadarView, ...]


@dataclass(frozen=True)
class StockRadarView:
    """单个 watchlist 标的的价格趋势和风险。"""

    instrument_id: str
    symbol: str
    sector_id: str
    momentum_126_21: RadarMetricView
    sector_relative_momentum_126_21: RadarMetricView
    distance_ma_200: RadarMetricView
    realized_volatility_20: RadarMetricView
    max_drawdown_126: RadarMetricView
    atr_20_ratio: RadarMetricView


@dataclass(frozen=True)
class StockRadarPageView:
    """支持服务端筛选、排序和分页的 watchlist 快照。"""

    source_state: SourceState
    observed_at_utc: datetime
    as_of_date: date | None
    calculated_at_utc: datetime | None
    items: tuple[StockRadarView, ...]
    offset: int
    limit: int
    has_more: bool


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

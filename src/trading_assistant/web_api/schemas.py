"""OpenAPI 对外响应模型。"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from trading_assistant.application.models import (
    BreadthMetricValidity,
    MarketBreadthValidity,
    RadarMetricValidity,
    RadarModuleState,
    Scalar,
    SourceState,
)


class ApiSchema(BaseModel):
    """允许直接验证应用层 dataclass 的响应基类。"""

    model_config = ConfigDict(from_attributes=True)


class ProblemResponse(ApiSchema):
    """统一错误响应。"""

    code: str
    title: str
    detail: str


class HealthResponse(ApiSchema):
    """进程级健康状态。"""

    status: Literal["ok"]
    checked_at_utc: datetime


class PositionResponse(ApiSchema):
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


class PortfolioResponse(ApiSchema):
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
    positions: list[PositionResponse]


class AccountHistoryPointResponse(ApiSchema):
    timestamp_utc: datetime
    account_id: str
    currency: str
    net_liquidation: float
    free_cash: float
    locked_cash: float


class AccountHistoryPageResponse(ApiSchema):
    items: list[AccountHistoryPointResponse]
    offset: int
    limit: int
    has_more: bool


class StrategyResponse(ApiSchema):
    source_state: SourceState
    observed_at_utc: datetime
    name: str | None
    approval_mode: str | None
    signal_expiry_hours: int | None
    parameters: dict[str, Scalar]
    risk_limits: dict[str, Scalar]


class FactorScoreResponse(ApiSchema):
    canonical_id: str
    symbol: str
    security_id: str
    score: float
    eligible: bool
    rank: int | None
    selected: bool
    target_weight: float


class FactorSnapshotResponse(ApiSchema):
    source_state: SourceState
    observed_at_utc: datetime
    asof_date: str | None
    available_at_utc: datetime | None
    batch_id: str | None
    delivery_id: str | None
    model_release_id: str | None
    source_kind: str | None
    expected_rows: int
    scores: list[FactorScoreResponse]


class SignalResponse(ApiSchema):
    event_id: str
    timestamp_utc: datetime
    strategy_name: str
    instrument_id: str
    direction: str
    target_weight: float
    reason: str
    status: str


class SignalPageResponse(ApiSchema):
    items: list[SignalResponse]
    offset: int
    limit: int
    has_more: bool


class WorkflowResponse(ApiSchema):
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


class WorkflowPageResponse(ApiSchema):
    items: list[WorkflowResponse]
    offset: int
    limit: int
    has_more: bool


class DecisionResponse(ApiSchema):
    timestamp_utc: datetime
    event_id: str
    strategy_name: str
    approval_mode: str
    decision: str
    reason: str


class OrderEventResponse(ApiSchema):
    timestamp_utc: datetime
    event_id: str
    instrument_id: str
    client_order_id: str
    status: str
    direction: str
    quantity: float
    reason: str


class FillResponse(ApiSchema):
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


class TimelineEventResponse(ApiSchema):
    timestamp_utc: datetime
    kind: Literal["signal", "decision", "order", "fill"]
    status: str
    title: str
    detail: str


class WorkflowDetailResponse(ApiSchema):
    workflow: WorkflowResponse
    target_weights: list[tuple[str, float]]
    planned_orders: list[dict[str, Scalar]]
    decisions: list[DecisionResponse]
    orders: list[OrderEventResponse]
    fills: list[FillResponse]
    timeline: list[TimelineEventResponse]


class OrderSummaryResponse(ApiSchema):
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


class OrderPageResponse(ApiSchema):
    items: list[OrderSummaryResponse]
    offset: int
    limit: int
    has_more: bool


class OrderDetailResponse(ApiSchema):
    summary: OrderSummaryResponse
    events: list[OrderEventResponse]
    fills: list[FillResponse]


class FillPageResponse(ApiSchema):
    items: list[FillResponse]
    offset: int
    limit: int
    has_more: bool


class BacktestRunResponse(ApiSchema):
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


class BacktestPageResponse(ApiSchema):
    items: list[BacktestRunResponse]
    offset: int
    limit: int
    has_more: bool


class BacktestDetailResponse(ApiSchema):
    run: BacktestRunResponse
    summary: dict[str, Scalar]
    available_tables: list[str]


class ReportTableResponse(ApiSchema):
    columns: list[str]
    rows: list[dict[str, Scalar]]
    offset: int
    limit: int
    has_more: bool


class CatalogCoverageResponse(ApiSchema):
    instrument_id: str
    symbol: str
    price_kind: Literal["signal", "execution"]
    bar_type: str
    state: SourceState
    first_at_utc: datetime | None
    last_at_utc: datetime | None


class CatalogResponse(ApiSchema):
    source_state: SourceState
    observed_at_utc: datetime
    provider: str
    coverage: list[CatalogCoverageResponse]


class QualityIssueResponse(ApiSchema):
    code: str
    severity: str
    instrument_id: str
    timestamp_utc: datetime | None
    message: str


class QualityInstrumentResponse(ApiSchema):
    instrument_id: str
    bar_count: int
    first_at_utc: datetime | None
    last_at_utc: datetime | None
    issue_count: int


class DataQualityResponse(ApiSchema):
    source_state: SourceState
    observed_at_utc: datetime
    generated_at_utc: datetime | None
    mode: str | None
    bars_fetched: int
    bars_written: int
    corporate_actions_written: int
    error_count: int
    warning_count: int
    issues: list[QualityIssueResponse]
    instruments: list[QualityInstrumentResponse]


class RadarMetricResponse(ApiSchema):
    value: float | None
    validity: RadarMetricValidity
    observations: int
    required: int


class RadarCoverageResponse(ApiSchema):
    eligible: int
    observed: int
    ratio: float


class RadarFreshnessResponse(ApiSchema):
    membership_age_days: int
    stale_after_days: int


class BreadthMetricResponse(ApiSchema):
    value: float | None
    validity: BreadthMetricValidity
    coverage: RadarCoverageResponse
    history_required: int


class MarketBreadthResponse(ApiSchema):
    source_state: SourceState
    observed_at_utc: datetime
    validity: MarketBreadthValidity
    as_of_date: date | None
    calculated_at_utc: datetime | None
    membership_date: date | None
    membership_source: str | None
    freshness: RadarFreshnessResponse | None
    b50: BreadthMetricResponse | None
    b200: BreadthMetricResponse | None
    ad10: BreadthMetricResponse | None
    nhnl: BreadthMetricResponse | None


class RadarMarketResponse(ApiSchema):
    spy_return_20: RadarMetricResponse
    spy_distance_ma_200: RadarMetricResponse
    rsp_spy_return_20: RadarMetricResponse


class RadarModuleResponse(ApiSchema):
    module_id: str
    label: str
    state: RadarModuleState
    detail: str


class MarketRadarSummaryResponse(ApiSchema):
    source_state: SourceState
    observed_at_utc: datetime
    as_of_date: date | None
    calculated_at_utc: datetime | None
    coverage: RadarCoverageResponse | None
    market: RadarMarketResponse | None
    modules: list[RadarModuleResponse]


class SectorRadarResponse(ApiSchema):
    sector_id: str
    instrument_id: str
    relative_strength_20: RadarMetricResponse
    relative_strength_60: RadarMetricResponse


class SectorRadarListResponse(ApiSchema):
    source_state: SourceState
    observed_at_utc: datetime
    as_of_date: date | None
    calculated_at_utc: datetime | None
    items: list[SectorRadarResponse]


class StockRadarResponse(ApiSchema):
    instrument_id: str
    symbol: str
    sector_id: str
    momentum_126_21: RadarMetricResponse
    sector_relative_momentum_126_21: RadarMetricResponse
    distance_ma_200: RadarMetricResponse
    realized_volatility_20: RadarMetricResponse
    max_drawdown_126: RadarMetricResponse
    atr_20_ratio: RadarMetricResponse


class StockRadarPageResponse(ApiSchema):
    source_state: SourceState
    observed_at_utc: datetime
    as_of_date: date | None
    calculated_at_utc: datetime | None
    items: list[StockRadarResponse]
    offset: int
    limit: int
    has_more: bool


class SourceStatusResponse(ApiSchema):
    name: str
    state: SourceState
    observed_at_utc: datetime
    last_event_at_utc: datetime | None
    detail: str


class SystemStatusResponse(ApiSchema):
    observed_at_utc: datetime
    sources: list[SourceStatusResponse]


class OverviewResponse(ApiSchema):
    observed_at_utc: datetime
    portfolio: PortfolioResponse
    active_strategy: StrategyResponse
    latest_factor: FactorSnapshotResponse
    workflow_status_counts: dict[str, int]
    latest_workflow_at_utc: datetime | None
    latest_fill_at_utc: datetime | None
    data_quality_state: SourceState


ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    404: {"model": ProblemResponse, "description": "查询对象不存在"},
    422: {"model": ProblemResponse, "description": "请求参数无效"},
    503: {"model": ProblemResponse, "description": "只读数据源无法读取"},
}

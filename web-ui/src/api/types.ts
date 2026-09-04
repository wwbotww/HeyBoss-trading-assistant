import type { components, operations } from './schema'

export type Health = components['schemas']['HealthResponse']
export type Overview = components['schemas']['OverviewResponse']
export type Portfolio = components['schemas']['PortfolioResponse']
export type Position = components['schemas']['PositionResponse']
export type AccountHistoryPage = components['schemas']['AccountHistoryPageResponse']
export type AccountHistoryPoint = components['schemas']['AccountHistoryPointResponse']
export type Strategy = components['schemas']['StrategyResponse']
export type FactorSnapshot = components['schemas']['FactorSnapshotResponse']
export type FactorScore = components['schemas']['FactorScoreResponse']
export type SignalPage = components['schemas']['SignalPageResponse']
export type Signal = components['schemas']['SignalResponse']
export type WorkflowPage = components['schemas']['WorkflowPageResponse']
export type Workflow = components['schemas']['WorkflowResponse']
export type WorkflowDetail = components['schemas']['WorkflowDetailResponse']
export type OrderPage = components['schemas']['OrderPageResponse']
export type OrderSummary = components['schemas']['OrderSummaryResponse']
export type OrderDetail = components['schemas']['OrderDetailResponse']
export type FillPage = components['schemas']['FillPageResponse']
export type Fill = components['schemas']['FillResponse']
export type BacktestPage = components['schemas']['BacktestPageResponse']
export type BacktestRun = components['schemas']['BacktestRunResponse']
export type BacktestDetail = components['schemas']['BacktestDetailResponse']
export type ReportTable = components['schemas']['ReportTableResponse']
export type Catalog = components['schemas']['CatalogResponse']
export type CatalogCoverage = components['schemas']['CatalogCoverageResponse']
export type DataQuality = components['schemas']['DataQualityResponse']
export type SystemStatus = components['schemas']['SystemStatusResponse']
export type SourceStatus = components['schemas']['SourceStatusResponse']
export type MarketRadarSummary = components['schemas']['MarketRadarSummaryResponse']
export type MarketBreadth = components['schemas']['MarketBreadthResponse']
export type BreadthMetric = components['schemas']['BreadthMetricResponse']
export type RadarMetric = components['schemas']['RadarMetricResponse']
export type RadarModule = components['schemas']['RadarModuleResponse']
export type SectorRadarList = components['schemas']['SectorRadarListResponse']
export type SectorRadar = components['schemas']['SectorRadarResponse']
export type StockRadarPage = components['schemas']['StockRadarPageResponse']
export type StockRadar = components['schemas']['StockRadarResponse']
export type Problem = components['schemas']['ProblemResponse']
export type SourceState = Portfolio['source_state']

export type PortfolioHistoryQuery = NonNullable<
  operations['listPortfolioHistory']['parameters']['query']
>
export type WorkflowQuery = NonNullable<operations['listWorkflows']['parameters']['query']>
export type SignalQuery = NonNullable<operations['listSignals']['parameters']['query']>
export type OrderQuery = NonNullable<operations['listOrders']['parameters']['query']>
export type FillQuery = NonNullable<operations['listFills']['parameters']['query']>
export type BacktestQuery = NonNullable<operations['listBacktests']['parameters']['query']>
export type ReportTableQuery = NonNullable<operations['getBacktestEquity']['parameters']['query']>
export type StockRadarQuery = NonNullable<
  operations['listMarketRadarStocks']['parameters']['query']
>

export type BacktestTableName = 'equity' | 'orders' | 'fills' | 'positions' | 'account'

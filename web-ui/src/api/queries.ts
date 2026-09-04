import { queryOptions } from '@tanstack/vue-query'

import {
  fetchActiveStrategy,
  fetchBacktest,
  fetchBacktests,
  fetchBacktestTable,
  fetchCatalogCoverage,
  fetchFills,
  fetchHealth,
  fetchLatestDataQuality,
  fetchLatestFactor,
  fetchMarketRadarBreadth,
  fetchMarketRadarSector,
  fetchMarketRadarSectors,
  fetchMarketRadarStock,
  fetchMarketRadarStocks,
  fetchMarketRadarSummary,
  fetchOrder,
  fetchOrders,
  fetchOverview,
  fetchPortfolio,
  fetchPortfolioHistory,
  fetchSignals,
  fetchSystemStatus,
  fetchWorkflow,
  fetchWorkflows,
} from './client'
import type {
  BacktestQuery,
  BacktestTableName,
  FillQuery,
  OrderQuery,
  PortfolioHistoryQuery,
  ReportTableQuery,
  SignalQuery,
  StockRadarQuery,
  WorkflowQuery,
} from './types'

export const queryKeys = {
  health: ['health'] as const,
  overview: ['overview'] as const,
  portfolio: ['portfolio'] as const,
  portfolioHistory: (query: PortfolioHistoryQuery) => ['portfolio', 'history', query] as const,
  signals: (query: SignalQuery) => ['signals', query] as const,
  workflows: (query: WorkflowQuery) => ['workflows', query] as const,
  workflow: (eventId: string) => ['workflows', 'detail', eventId] as const,
  orders: (query: OrderQuery) => ['orders', query] as const,
  order: (clientOrderId: string) => ['orders', 'detail', clientOrderId] as const,
  fills: (query: FillQuery) => ['fills', query] as const,
  strategy: ['strategy', 'active'] as const,
  factor: ['factors', 'latest'] as const,
  backtests: (query: BacktestQuery) => ['backtests', query] as const,
  backtest: (runId: string) => ['backtests', 'detail', runId] as const,
  backtestTable: (runId: string, table: BacktestTableName, query: ReportTableQuery) =>
    ['backtests', 'report', runId, table, query] as const,
  catalog: ['data', 'catalog'] as const,
  dataQuality: ['data', 'quality', 'latest'] as const,
  system: ['system', 'status'] as const,
  marketRadarSummary: ['market-radar', 'summary'] as const,
  marketRadarBreadth: ['market-radar', 'breadth'] as const,
  marketRadarSectors: ['market-radar', 'sectors'] as const,
  marketRadarSector: (sectorId: string) => ['market-radar', 'sectors', sectorId] as const,
  marketRadarStocks: (query: StockRadarQuery) => ['market-radar', 'stocks', query] as const,
  marketRadarStock: (instrumentId: string) => ['market-radar', 'stocks', instrumentId] as const,
}

export const healthQuery = () => queryOptions({ queryKey: queryKeys.health, queryFn: fetchHealth })
export const overviewQuery = () =>
  queryOptions({ queryKey: queryKeys.overview, queryFn: fetchOverview })
export const portfolioQuery = () =>
  queryOptions({ queryKey: queryKeys.portfolio, queryFn: fetchPortfolio })
export const portfolioHistoryQuery = (query: PortfolioHistoryQuery = { offset: 0, limit: 50 }) =>
  queryOptions({
    queryKey: queryKeys.portfolioHistory(query),
    queryFn: () => fetchPortfolioHistory(query),
  })
export const signalsQuery = (query: SignalQuery = { offset: 0, limit: 50 }) =>
  queryOptions({ queryKey: queryKeys.signals(query), queryFn: () => fetchSignals(query) })
export const workflowsQuery = (query: WorkflowQuery = { offset: 0, limit: 50 }) =>
  queryOptions({ queryKey: queryKeys.workflows(query), queryFn: () => fetchWorkflows(query) })
export const workflowQuery = (eventId: string, enabled = true) =>
  queryOptions({
    queryKey: queryKeys.workflow(eventId),
    queryFn: () => fetchWorkflow(eventId),
    enabled,
  })
export const ordersQuery = (query: OrderQuery = { offset: 0, limit: 50 }) =>
  queryOptions({ queryKey: queryKeys.orders(query), queryFn: () => fetchOrders(query) })
export const orderQuery = (clientOrderId: string, enabled = true) =>
  queryOptions({
    queryKey: queryKeys.order(clientOrderId),
    queryFn: () => fetchOrder(clientOrderId),
    enabled,
  })
export const fillsQuery = (query: FillQuery = { offset: 0, limit: 50 }) =>
  queryOptions({ queryKey: queryKeys.fills(query), queryFn: () => fetchFills(query) })
export const strategyQuery = () =>
  queryOptions({ queryKey: queryKeys.strategy, queryFn: fetchActiveStrategy })
export const factorQuery = () =>
  queryOptions({ queryKey: queryKeys.factor, queryFn: fetchLatestFactor })
export const backtestsQuery = (query: BacktestQuery) =>
  queryOptions({ queryKey: queryKeys.backtests(query), queryFn: () => fetchBacktests(query) })
export const backtestQuery = (runId: string, enabled = true) =>
  queryOptions({
    queryKey: queryKeys.backtest(runId),
    queryFn: () => fetchBacktest(runId),
    enabled,
  })
export const backtestTableQuery = (
  runId: string,
  table: BacktestTableName,
  query: ReportTableQuery,
  enabled = true,
) =>
  queryOptions({
    queryKey: queryKeys.backtestTable(runId, table, query),
    queryFn: () => fetchBacktestTable(runId, table, query),
    enabled,
  })
export const catalogQuery = () =>
  queryOptions({ queryKey: queryKeys.catalog, queryFn: fetchCatalogCoverage })
export const dataQualityQuery = () =>
  queryOptions({ queryKey: queryKeys.dataQuality, queryFn: fetchLatestDataQuality })
export const systemQuery = () =>
  queryOptions({ queryKey: queryKeys.system, queryFn: fetchSystemStatus })
export const marketRadarSummaryQuery = () =>
  queryOptions({ queryKey: queryKeys.marketRadarSummary, queryFn: fetchMarketRadarSummary })
export const marketRadarBreadthQuery = () =>
  queryOptions({ queryKey: queryKeys.marketRadarBreadth, queryFn: fetchMarketRadarBreadth })
export const marketRadarSectorsQuery = () =>
  queryOptions({ queryKey: queryKeys.marketRadarSectors, queryFn: fetchMarketRadarSectors })
export const marketRadarSectorQuery = (sectorId: string, enabled = true) =>
  queryOptions({
    queryKey: queryKeys.marketRadarSector(sectorId),
    queryFn: () => fetchMarketRadarSector(sectorId),
    enabled,
  })
export const marketRadarStocksQuery = (query: StockRadarQuery) =>
  queryOptions({
    queryKey: queryKeys.marketRadarStocks(query),
    queryFn: () => fetchMarketRadarStocks(query),
  })
export const marketRadarStockQuery = (instrumentId: string, enabled = true) =>
  queryOptions({
    queryKey: queryKeys.marketRadarStock(instrumentId),
    queryFn: () => fetchMarketRadarStock(instrumentId),
    enabled,
  })

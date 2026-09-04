import createClient from 'openapi-fetch'

import type { paths } from './schema'
import type {
  AccountHistoryPage,
  BacktestDetail,
  BacktestPage,
  BacktestQuery,
  BacktestTableName,
  Catalog,
  DataQuality,
  FactorSnapshot,
  FillQuery,
  FillPage,
  Health,
  MacroRegime,
  MarketBreadth,
  MarketEarnings,
  MarketRadarSummary,
  OrderDetail,
  OrderQuery,
  OrderPage,
  Overview,
  Portfolio,
  PortfolioHistoryQuery,
  Problem,
  ReportTable,
  ReportTableQuery,
  SectorRadar,
  SectorRadarList,
  SignalQuery,
  SignalPage,
  Strategy,
  StockRadar,
  StockRadarPage,
  StockRadarQuery,
  SystemStatus,
  WorkflowDetail,
  WorkflowQuery,
  WorkflowPage,
} from './types'

const http = createClient<paths>({
  baseUrl: globalThis.location.origin,
  fetch: (request) => globalThis.fetch(request),
  headers: {
    Accept: 'application/json, application/problem+json',
  },
})

interface ReadResult<T> {
  data?: T
  error?: unknown
  response: Response
}

export class ApiError extends Error {
  readonly code: string
  readonly status: number | null

  constructor(code: string, message: string, status: number | null = null) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.status = status
  }
}

function isProblem(value: unknown): value is Problem {
  if (typeof value !== 'object' || value === null) {
    return false
  }
  const candidate = value as Record<string, unknown>
  return (
    typeof candidate.code === 'string' &&
    typeof candidate.title === 'string' &&
    typeof candidate.detail === 'string'
  )
}

async function read<T>(operation: Promise<ReadResult<T>>): Promise<T> {
  try {
    const result = await operation
    if (result.data !== undefined) {
      return result.data
    }
    if (isProblem(result.error)) {
      throw new ApiError(result.error.code, result.error.detail, result.response.status)
    }
    throw new ApiError('api_error', '服务返回了无法识别的错误。', result.response.status)
  } catch (error: unknown) {
    if (error instanceof ApiError) {
      throw error
    }
    throw new ApiError('network_error', '无法连接只读 Web API。')
  }
}

export function fetchHealth(): Promise<Health> {
  return read(http.GET('/api/health'))
}

export function fetchOverview(): Promise<Overview> {
  return read(http.GET('/api/overview'))
}

export function fetchPortfolio(): Promise<Portfolio> {
  return read(http.GET('/api/portfolio'))
}

export function fetchPortfolioHistory(
  query: PortfolioHistoryQuery = { offset: 0, limit: 50 },
): Promise<AccountHistoryPage> {
  return read(
    http.GET('/api/portfolio/history', {
      params: { query },
    }),
  )
}

export function fetchSignals(query: SignalQuery = { offset: 0, limit: 50 }): Promise<SignalPage> {
  return read(
    http.GET('/api/signals', {
      params: { query },
    }),
  )
}

export function fetchWorkflows(
  query: WorkflowQuery = { offset: 0, limit: 50 },
): Promise<WorkflowPage> {
  return read(
    http.GET('/api/workflows', {
      params: { query },
    }),
  )
}

export async function fetchWorkflow(eventId: string): Promise<WorkflowDetail> {
  const detail = await read(
    http.GET('/api/workflows/{event_id}', {
      params: { path: { event_id: eventId } },
    }),
  )
  const targetWeights = detail.target_weights.map((entry) => {
    if (entry.length !== 2 || typeof entry[0] !== 'string' || typeof entry[1] !== 'number') {
      throw new ApiError('invalid_response', '工作流目标权重结构无效。')
    }
    return [entry[0], entry[1]] as [string, number]
  })
  return { ...detail, target_weights: targetWeights }
}

export function fetchOrders(query: OrderQuery = { offset: 0, limit: 50 }): Promise<OrderPage> {
  return read(
    http.GET('/api/orders', {
      params: { query },
    }),
  )
}

export function fetchOrder(clientOrderId: string): Promise<OrderDetail> {
  return read(
    http.GET('/api/orders/{client_order_id}', {
      params: { path: { client_order_id: clientOrderId } },
    }),
  )
}

export function fetchFills(query: FillQuery = { offset: 0, limit: 50 }): Promise<FillPage> {
  return read(
    http.GET('/api/fills', {
      params: { query },
    }),
  )
}

export function fetchActiveStrategy(): Promise<Strategy> {
  return read(http.GET('/api/strategy/active'))
}

export function fetchLatestFactor(): Promise<FactorSnapshot> {
  return read(http.GET('/api/factors/latest'))
}

export function fetchBacktests(query: BacktestQuery): Promise<BacktestPage> {
  return read(http.GET('/api/backtests', { params: { query } }))
}

export function fetchBacktest(runId: string): Promise<BacktestDetail> {
  return read(
    http.GET('/api/backtests/{run_id}', {
      params: { path: { run_id: runId } },
    }),
  )
}

export function fetchBacktestTable(
  runId: string,
  table: BacktestTableName,
  query: ReportTableQuery,
): Promise<ReportTable> {
  const params = { path: { run_id: runId }, query }
  switch (table) {
    case 'equity':
      return read(http.GET('/api/backtests/{run_id}/equity', { params }))
    case 'orders':
      return read(http.GET('/api/backtests/{run_id}/orders', { params }))
    case 'fills':
      return read(http.GET('/api/backtests/{run_id}/fills', { params }))
    case 'positions':
      return read(http.GET('/api/backtests/{run_id}/positions', { params }))
    case 'account':
      return read(http.GET('/api/backtests/{run_id}/account', { params }))
  }
}

export function fetchCatalogCoverage(): Promise<Catalog> {
  return read(http.GET('/api/data/catalog'))
}

export function fetchLatestDataQuality(): Promise<DataQuality> {
  return read(http.GET('/api/data/quality/latest'))
}

export function fetchSystemStatus(): Promise<SystemStatus> {
  return read(http.GET('/api/system/status'))
}

export function fetchMarketRadarSummary(): Promise<MarketRadarSummary> {
  return read(http.GET('/api/market-radar/summary'))
}

export function fetchMarketRadarBreadth(): Promise<MarketBreadth> {
  return read(http.GET('/api/market-radar/breadth'))
}

export function fetchMarketRadarMacro(): Promise<MacroRegime> {
  return read(http.GET('/api/market-radar/macro'))
}

export function fetchMarketRadarEarnings(): Promise<MarketEarnings> {
  return read(http.GET('/api/market-radar/earnings'))
}

export function fetchMarketRadarSectors(): Promise<SectorRadarList> {
  return read(http.GET('/api/market-radar/sectors'))
}

export function fetchMarketRadarSector(sectorId: string): Promise<SectorRadar> {
  return read(
    http.GET('/api/market-radar/sectors/{sector_id}', {
      params: { path: { sector_id: sectorId } },
    }),
  )
}

export function fetchMarketRadarStocks(query: StockRadarQuery): Promise<StockRadarPage> {
  return read(http.GET('/api/market-radar/stocks', { params: { query } }))
}

export function fetchMarketRadarStock(instrumentId: string): Promise<StockRadar> {
  return read(
    http.GET('/api/market-radar/stocks/{instrument_id}', {
      params: { path: { instrument_id: instrumentId } },
    }),
  )
}

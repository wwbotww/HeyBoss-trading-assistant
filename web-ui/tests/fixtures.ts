import { vi, type Mock } from 'vitest'

import type {
  AccountHistoryPage,
  BacktestDetail,
  BacktestPage,
  Catalog,
  DataQuality,
  FactorSnapshot,
  FillPage,
  Health,
  MarketBreadth,
  MarketRadarSummary,
  OrderDetail,
  OrderPage,
  Overview,
  Portfolio,
  ReportTable,
  SectorRadarList,
  SignalPage,
  Strategy,
  StockRadarPage,
  SystemStatus,
  WorkflowDetail,
  WorkflowPage,
} from '../src/api/types'

function firstItem<T>(items: readonly T[]): T {
  const item = items[0]
  if (item === undefined) {
    throw new Error('测试夹具至少需要一条记录')
  }
  return item
}

export const healthFixture = {
  status: 'ok',
  checked_at_utc: '2026-08-15T12:00:00Z',
} satisfies Health

export const portfolioFixture = {
  source_state: 'available',
  observed_at_utc: '2026-08-15T12:00:00Z',
  snapshot_at_utc: '2026-08-15T11:58:00Z',
  account_id: 'DU***42',
  currency: 'USD',
  net_liquidation: 125_430.25,
  free_cash: 48_200.5,
  locked_cash: 1_500,
  age_seconds: 120,
  is_stale: false,
  positions: [
    {
      canonical_id: 'AAPL.US',
      source_instrument_id: 'AAPL.NASDAQ',
      symbol: 'AAPL',
      side: 'LONG',
      signed_quantity: 25,
      avg_open_price: 201.2,
      realized_pnl: 112.5,
      reference_price: 205.4,
      reference_price_at_utc: '2026-08-14T20:00:00Z',
      estimated_market_value: 5_135,
      estimated_weight: 0.04094,
      price_kind: 'EOD adjusted close',
    },
  ],
} satisfies Portfolio

export const overviewFixture = {
  observed_at_utc: '2026-08-15T12:00:00Z',
  portfolio: portfolioFixture,
  active_strategy: {
    source_state: 'available',
    observed_at_utc: '2026-08-15T12:00:00Z',
    name: 'patchtst_e3',
    approval_mode: 'manual',
    signal_expiry_hours: 24,
    parameters: { top_n: 3 },
    risk_limits: { max_total_position_weight: 0.8 },
  },
  latest_factor: {
    source_state: 'available',
    observed_at_utc: '2026-08-15T12:00:00Z',
    asof_date: '2026-08-14',
    available_at_utc: '2026-08-15T00:30:00Z',
    batch_id: 'batch-20260814',
    delivery_id: 'delivery-20260814',
    model_release_id: 'patchtst-e3-demo',
    source_kind: 'signal_inference',
    expected_rows: 10,
    scores: [
      {
        canonical_id: 'AAPL.US',
        symbol: 'AAPL',
        security_id: 'eodhd:isin:US0378331005',
        score: 0.72,
        eligible: true,
        rank: 1,
        selected: true,
        target_weight: 0.3333,
      },
    ],
  },
  workflow_status_counts: { NEW: 2, FILLED: 4 },
  latest_workflow_at_utc: '2026-08-15T11:30:00Z',
  latest_fill_at_utc: '2026-08-15T11:42:00Z',
  data_quality_state: 'available',
} satisfies Overview

export const historyFixture = {
  items: [
    {
      timestamp_utc: '2026-08-15T11:58:00Z',
      account_id: 'DU***42',
      currency: 'USD',
      net_liquidation: 125_430.25,
      free_cash: 48_200.5,
      locked_cash: 1_500,
    },
  ],
  offset: 0,
  limit: 50,
  has_more: false,
} satisfies AccountHistoryPage

export const workflowsFixture = {
  items: [
    {
      event_id: 'evt-20260815-001',
      strategy_name: 'patchtst_e3',
      rebalance_key: '2026-08-15',
      signal_timestamp_utc: '2026-08-15T11:30:00Z',
      expires_at_utc: '2026-08-16T11:30:00Z',
      status: 'PENDING_APPROVAL',
      reason: 'PatchTST E3 top-ranked cross-section',
      target_count: 3,
      planned_order_count: 2,
      risk_summary: 'All application risk checks passed',
    },
  ],
  offset: 0,
  limit: 50,
  has_more: false,
} satisfies WorkflowPage

export const signalsFixture = {
  items: [
    {
      event_id: 'evt-20260815-001',
      timestamp_utc: '2026-08-15T11:30:00Z',
      strategy_name: 'patchtst_e3',
      instrument_id: 'AAPL.US',
      direction: 'BUY',
      target_weight: 0.3333,
      reason: 'Rank 1 factor score',
      status: 'NEW',
    },
  ],
  offset: 0,
  limit: 50,
  has_more: false,
} satisfies SignalPage

export const ordersFixture = {
  items: [
    {
      client_order_id: 'O-20260815-001',
      event_id: 'evt-20260815-001',
      instrument_id: 'AAPL.US',
      direction: 'BUY',
      quantity: 10,
      status: 'FILLED',
      first_event_at_utc: '2026-08-15T11:35:00Z',
      latest_event_at_utc: '2026-08-15T11:42:00Z',
      event_count: 3,
      fill_count: 1,
      filled_quantity: 10,
    },
  ],
  offset: 0,
  limit: 50,
  has_more: false,
} satisfies OrderPage

export const fillsFixture = {
  items: [
    {
      trade_id: 'T-20260815-001',
      timestamp_utc: '2026-08-15T11:42:00Z',
      event_id: 'evt-20260815-001',
      strategy_name: 'patchtst_e3',
      instrument_id: 'AAPL.US',
      client_order_id: 'O-20260815-001',
      direction: 'BUY',
      quantity: 10,
      price: 205.25,
      commission: 1,
    },
  ],
  offset: 0,
  limit: 50,
  has_more: false,
} satisfies FillPage

export const strategyFixture = overviewFixture.active_strategy satisfies Strategy
export const factorFixture = {
  ...overviewFixture.latest_factor,
  scores: [
    firstItem(overviewFixture.latest_factor.scores),
    {
      canonical_id: 'MSFT.US',
      symbol: 'MSFT',
      security_id: 'eodhd:isin:US5949181045',
      score: 0.48,
      eligible: true,
      rank: 2,
      selected: true,
      target_weight: 0.3333,
    },
    {
      canonical_id: 'NVDA.US',
      symbol: 'NVDA',
      security_id: 'eodhd:isin:US67066G1040',
      score: 0.31,
      eligible: true,
      rank: 3,
      selected: false,
      target_weight: 0,
    },
  ],
  expected_rows: 3,
} satisfies FactorSnapshot

export const workflowDetailFixture = {
  workflow: firstItem(workflowsFixture.items),
  target_weights: [
    ['AAPL.US', 0.3333],
    ['MSFT.US', 0.3333],
  ],
  planned_orders: [{ instrument_id: 'AAPL.US', direction: 'BUY', quantity: 10 }],
  decisions: [
    {
      timestamp_utc: '2026-08-15T11:31:00Z',
      event_id: 'evt-20260815-001',
      strategy_name: 'patchtst_e3',
      approval_mode: 'manual',
      decision: 'APPROVED',
      reason: 'approved',
    },
  ],
  orders: [
    {
      timestamp_utc: '2026-08-15T11:35:00Z',
      event_id: 'evt-20260815-001',
      instrument_id: 'AAPL.US',
      client_order_id: 'O-20260815-001',
      status: 'FILLED',
      direction: 'BUY',
      quantity: 10,
      reason: 'filled',
    },
  ],
  fills: fillsFixture.items,
  timeline: [
    {
      timestamp_utc: '2026-08-15T11:30:00Z',
      kind: 'signal',
      status: 'NEW',
      title: '策略信号',
      detail: 'PatchTST factor rank',
    },
    {
      timestamp_utc: '2026-08-15T11:42:00Z',
      kind: 'fill',
      status: 'FILLED',
      title: '订单成交',
      detail: 'AAPL.US filled',
    },
  ],
} satisfies WorkflowDetail

export const orderDetailFixture = {
  summary: firstItem(ordersFixture.items),
  events: workflowDetailFixture.orders,
  fills: fillsFixture.items,
} satisfies OrderDetail

export const backtestsFixture = {
  items: [
    {
      run_id: 'web-run',
      started_at_utc: '2026-08-13T00:00:00Z',
      completed_at_utc: '2026-08-14T00:00:00Z',
      status: 'COMPLETED',
      strategy_name: 'patchtst_e3',
      evaluation_start: '2026-01-01',
      end: '2026-08-12',
      final_equity_usd: 105_000,
      annualized_return: 0.1,
      max_drawdown: -0.05,
      sharpe_ratio: 1.1,
      report_state: 'available',
    },
  ],
  offset: 0,
  limit: 20,
  has_more: false,
} satisfies BacktestPage

export const backtestDetailFixture = {
  run: firstItem(backtestsFixture.items),
  summary: {
    strategy: 'patchtst_e3',
    turnover: 0.42,
    fill_count: 8,
  },
  available_tables: ['equity', 'orders', 'fills', 'positions', 'account'],
} satisfies BacktestDetail

export const equityReportFixture = {
  columns: ['timestamp_utc', 'equity', 'cash', 'daily_return'],
  rows: [
    {
      timestamp_utc: '2026-01-01T00:00:00+00:00',
      equity: 100_000,
      cash: 100_000,
      daily_return: 0,
    },
    {
      timestamp_utc: '2026-01-02T00:00:00+00:00',
      equity: 105_000,
      cash: 60_000,
      daily_return: 0.05,
    },
  ],
  offset: 0,
  limit: 50,
  has_more: false,
} satisfies ReportTable

export const genericReportFixture = {
  columns: ['id', 'value'],
  rows: [{ id: 1, value: 2 }],
  offset: 0,
  limit: 50,
  has_more: false,
} satisfies ReportTable

export const catalogFixture = {
  source_state: 'available',
  observed_at_utc: '2026-08-15T12:00:00Z',
  provider: 'eodhd',
  coverage: [
    {
      instrument_id: 'AAPL.US',
      symbol: 'AAPL',
      price_kind: 'signal',
      bar_type: 'AAPL.US-1-DAY-LAST-INTERNAL',
      state: 'available',
      first_at_utc: '2024-01-01T00:00:00Z',
      last_at_utc: '2026-08-14T00:00:00Z',
    },
    {
      instrument_id: 'AAPL.US',
      symbol: 'AAPL',
      price_kind: 'execution',
      bar_type: 'AAPL.US-1-DAY-LAST-EXTERNAL',
      state: 'available',
      first_at_utc: '2024-01-01T00:00:00Z',
      last_at_utc: '2026-08-14T00:00:00Z',
    },
  ],
} satisfies Catalog

export const qualityFixture = {
  source_state: 'available',
  observed_at_utc: '2026-08-15T12:00:00Z',
  generated_at_utc: '2026-08-15T11:00:00Z',
  mode: 'validate',
  bars_fetched: 100,
  bars_written: 10,
  corporate_actions_written: 1,
  error_count: 0,
  warning_count: 1,
  issues: [
    {
      code: 'SHORT_HISTORY',
      severity: 'warning',
      instrument_id: 'AAPL.US',
      timestamp_utc: null,
      message: 'History is shorter than requested range',
    },
  ],
  instruments: [
    {
      instrument_id: 'AAPL.US',
      bar_count: 500,
      first_at_utc: '2024-01-01T00:00:00Z',
      last_at_utc: '2026-08-14T00:00:00Z',
      issue_count: 1,
    },
  ],
} satisfies DataQuality

export const systemFixture = {
  observed_at_utc: '2026-08-15T12:00:00Z',
  sources: [
    {
      name: 'live_database',
      state: 'available',
      observed_at_utc: '2026-08-15T12:00:00Z',
      last_event_at_utc: '2026-08-15T11:42:00Z',
      detail: 'Paper audit database is readable.',
    },
    {
      name: 'ibkr',
      state: 'unobserved',
      observed_at_utc: '2026-08-15T12:00:00Z',
      last_event_at_utc: null,
      detail: 'Web API does not inspect broker connectivity.',
    },
  ],
} satisfies SystemStatus

const completeMetric = (value: number) => ({
  value,
  validity: 'complete' as const,
  observations: 220,
  required: 20,
})

export const marketRadarSummaryFixture = {
  source_state: 'available',
  observed_at_utc: '2026-09-03T02:00:00Z',
  as_of_date: '2026-09-02',
  calculated_at_utc: '2026-09-03T01:00:00Z',
  coverage: { eligible: 25, observed: 25, ratio: 1 },
  market: {
    spy_return_20: completeMetric(0.04),
    spy_distance_ma_200: completeMetric(0.12),
    rsp_spy_return_20: completeMetric(-0.01),
  },
  modules: [
    {
      module_id: 'spy_trend',
      label: 'SPY 趋势',
      state: 'complete',
      detail: '20 日趋势与 MA200 距离均来自完整价格快照。',
    },
    {
      module_id: 'market_breadth',
      label: '市场宽度',
      state: 'complete',
      detail: 'SPY 当前持仓代理的四项宽度指标完整。',
    },
    {
      module_id: 'equal_weight',
      label: '等权确认',
      state: 'complete',
      detail: 'RSP/SPY 20 日相对表现可用。',
    },
    {
      module_id: 'real_rates',
      label: '实际利率',
      state: 'unavailable',
      detail: 'R4 宏观观测与日期对齐链路尚未实施。',
    },
    {
      module_id: 'risk_appetite',
      label: '风险偏好',
      state: 'unavailable',
      detail: 'R4 所需的波动率期限结构和信用组合尚未实施。',
    },
    {
      module_id: 'earnings_revisions',
      label: 'EPS 修正',
      state: 'unavailable',
      detail: 'R5 盈利预期端点当前无可用权限; 尚未采集。',
    },
  ],
} satisfies MarketRadarSummary

const completeBreadthMetric = (value: number, historyRequired: number) => ({
  value,
  validity: 'complete' as const,
  coverage: { eligible: 503, observed: 503, ratio: 1 },
  history_required: historyRequired,
})

export const marketBreadthFixture = {
  source_state: 'available',
  observed_at_utc: '2026-09-03T02:00:00Z',
  validity: 'complete',
  as_of_date: '2026-09-02',
  calculated_at_utc: '2026-09-03T01:00:00Z',
  membership_date: '2026-09-01',
  membership_source: 'state_street_spy_holdings',
  freshness: { membership_age_days: 2, stale_after_days: 7 },
  b50: completeBreadthMetric(0.481113, 50),
  b200: {
    ...completeBreadthMetric(0.664671, 200),
    coverage: { eligible: 503, observed: 501, ratio: 501 / 503 },
  },
  ad10: completeBreadthMetric(-0.15022, 11),
  nhnl: {
    ...completeBreadthMetric(0.022, 252),
    coverage: { eligible: 503, observed: 500, ratio: 500 / 503 },
  },
} satisfies MarketBreadth

export const marketRadarSectorsFixture = {
  source_state: 'available',
  observed_at_utc: '2026-09-03T02:00:00Z',
  as_of_date: '2026-09-02',
  calculated_at_utc: '2026-09-03T01:00:00Z',
  items: [
    {
      sector_id: 'information_technology',
      instrument_id: 'XLK.US',
      relative_strength_20: completeMetric(0.03),
      relative_strength_60: completeMetric(0.08),
    },
    {
      sector_id: 'energy',
      instrument_id: 'XLE.US',
      relative_strength_20: completeMetric(-0.02),
      relative_strength_60: completeMetric(-0.04),
    },
  ],
} satisfies SectorRadarList

export const marketRadarStocksFixture = {
  source_state: 'available',
  observed_at_utc: '2026-09-03T02:00:00Z',
  as_of_date: '2026-09-02',
  calculated_at_utc: '2026-09-03T01:00:00Z',
  items: [
    {
      instrument_id: 'AAPL.US',
      symbol: 'AAPL',
      sector_id: 'information_technology',
      momentum_126_21: completeMetric(0.15),
      sector_relative_momentum_126_21: completeMetric(0.07),
      distance_ma_200: completeMetric(0.11),
      realized_volatility_20: completeMetric(0.2),
      max_drawdown_126: completeMetric(-0.14),
      atr_20_ratio: completeMetric(0.025),
    },
    {
      instrument_id: 'XOM.US',
      symbol: 'XOM',
      sector_id: 'energy',
      momentum_126_21: completeMetric(0.06),
      sector_relative_momentum_126_21: completeMetric(0.01),
      distance_ma_200: completeMetric(0.03),
      realized_volatility_20: completeMetric(0.24),
      max_drawdown_126: completeMetric(-0.19),
      atr_20_ratio: completeMetric(0.03),
    },
  ],
  offset: 0,
  limit: 20,
  has_more: false,
} satisfies StockRadarPage

const defaultResponses: Readonly<Record<string, unknown>> = {
  '/api/health': healthFixture,
  '/api/overview': overviewFixture,
  '/api/portfolio': portfolioFixture,
  '/api/portfolio/history': historyFixture,
  '/api/workflows': workflowsFixture,
  '/api/signals': signalsFixture,
  '/api/orders': ordersFixture,
  '/api/orders/O-20260815-001': orderDetailFixture,
  '/api/fills': fillsFixture,
  '/api/workflows/evt-20260815-001': workflowDetailFixture,
  '/api/strategy/active': strategyFixture,
  '/api/factors/latest': factorFixture,
  '/api/backtests': backtestsFixture,
  '/api/backtests/web-run': backtestDetailFixture,
  '/api/backtests/web-run/equity': equityReportFixture,
  '/api/backtests/web-run/orders': genericReportFixture,
  '/api/backtests/web-run/fills': genericReportFixture,
  '/api/backtests/web-run/positions': genericReportFixture,
  '/api/backtests/web-run/account': genericReportFixture,
  '/api/data/catalog': catalogFixture,
  '/api/data/quality/latest': qualityFixture,
  '/api/system/status': systemFixture,
  '/api/market-radar/summary': marketRadarSummaryFixture,
  '/api/market-radar/breadth': marketBreadthFixture,
  '/api/market-radar/sectors': marketRadarSectorsFixture,
  '/api/market-radar/sectors/information_technology': firstItem(marketRadarSectorsFixture.items),
  '/api/market-radar/stocks': marketRadarStocksFixture,
  '/api/market-radar/stocks/AAPL.US': firstItem(marketRadarStocksFixture.items),
}

export type FetchFunction = (input: string | URL | Request, init?: RequestInit) => Promise<Response>

export function installApiMock(
  overrides: Readonly<Record<string, unknown>> = {},
): Mock<FetchFunction> {
  const responses = { ...defaultResponses, ...overrides }
  const fetchMock = vi.fn<FetchFunction>((input, init) => {
    const request = input instanceof Request ? input : new Request(input, init)
    const pathname = new URL(request.url).pathname
    const body = responses[pathname]
    if (body === undefined) {
      return Promise.resolve(
        new Response(
          JSON.stringify({ code: 'not_found', title: 'Not found', detail: 'Missing fixture' }),
          {
            status: 404,
            headers: { 'Content-Type': 'application/problem+json' },
          },
        ),
      )
    }
    return Promise.resolve(
      new Response(JSON.stringify(body), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

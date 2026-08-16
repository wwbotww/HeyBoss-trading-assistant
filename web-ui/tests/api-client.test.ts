import { beforeEach, describe, expect, it, vi } from 'vitest'

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
  fetchOrder,
  fetchOrders,
  fetchOverview,
  fetchPortfolio,
  fetchPortfolioHistory,
  fetchSignals,
  fetchSystemStatus,
  fetchWorkflow,
  fetchWorkflows,
} from '../src/api/client'
import { installApiMock, type FetchFunction } from './fixtures'

describe('只读 API client', () => {
  beforeEach(() => {
    installApiMock()
  })

  it('只通过 GET 读取完整 F3 接口', async () => {
    const fetchMock = installApiMock()

    const responses = await Promise.all([
      fetchHealth(),
      fetchOverview(),
      fetchPortfolio(),
      fetchPortfolioHistory(),
      fetchSignals(),
      fetchWorkflows(),
      fetchOrders(),
      fetchFills(),
      fetchWorkflow('evt-20260815-001'),
      fetchOrder('O-20260815-001'),
      fetchActiveStrategy(),
      fetchLatestFactor(),
      fetchBacktests({ offset: 0, limit: 20 }),
      fetchBacktest('web-run'),
      ...(['equity', 'orders', 'fills', 'positions', 'account'] as const).map((table) =>
        fetchBacktestTable('web-run', table, { offset: 0, limit: 50 }),
      ),
      fetchCatalogCoverage(),
      fetchLatestDataQuality(),
      fetchSystemStatus(),
    ])

    expect(responses).toHaveLength(22)
    expect(fetchMock).toHaveBeenCalledTimes(22)
    for (const call of fetchMock.mock.calls) {
      const request = call[0]
      expect(request).toBeInstanceOf(Request)
      if (request instanceof Request) {
        expect(request.method).toBe('GET')
        expect(new URL(request.url).pathname).toMatch(/^\/api\//)
      }
    }
  })

  it('把 problem+json 转为脱敏的 ApiError', async () => {
    const fetchMock = vi.fn<FetchFunction>(async () =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            code: 'source_unavailable',
            title: 'Source unavailable',
            detail: '只读数据源暂时无法读取。',
          }),
          {
            status: 503,
            headers: { 'Content-Type': 'application/problem+json' },
          },
        ),
      ),
    )
    vi.stubGlobal('fetch', fetchMock)

    await expect(fetchOverview()).rejects.toMatchObject({
      name: 'ApiError',
      code: 'source_unavailable',
      message: '只读数据源暂时无法读取。',
      status: 503,
    })
  })

  it('把网络异常收敛为统一错误', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn<FetchFunction>(() => Promise.reject(new TypeError('socket closed'))),
    )

    await expect(fetchHealth()).rejects.toMatchObject({
      code: 'network_error',
      message: '无法连接只读 Web API。',
      status: null,
    })
  })
})

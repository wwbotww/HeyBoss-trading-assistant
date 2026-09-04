import { flushPromises } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import MarketRadarPage from '../src/pages/MarketRadarPage.vue'
import { installApiMock, marketBreadthFixture, marketRadarSummaryFixture } from './fixtures'
import { mountPage } from './helpers'

describe('市场雷达页面', () => {
  it('总览展示真实价格和 SPY 当前持仓代理宽度', async () => {
    installApiMock()
    const { wrapper } = await mountPage(MarketRadarPage, '/market-radar?view=overview')
    await flushPromises()

    expect(wrapper.text()).toContain('市场雷达')
    expect(wrapper.text()).toContain('SPY 趋势')
    expect(wrapper.text()).toContain('市场宽度')
    expect(wrapper.text()).toContain('SPY 当前持仓代理')
    expect(wrapper.text()).toContain('State Street SPY 官方持仓')
    expect(wrapper.text()).toContain('SPY · 20 日收益')
    expect(wrapper.text()).toContain('4.0%')
    expect(wrapper.text()).toContain('25 / 25')
    expect(wrapper.text()).toContain('B50')
    expect(wrapper.text()).toContain('48.11%')
    expect(wrapper.text()).toContain('503 / 503')
    expect(wrapper.text()).toContain('501 / 503')
    expect(wrapper.text()).toContain('只描述当前结构，不代表历史 PIT 指数宽度')
    expect(wrapper.text()).toContain('数据库尚未保存可供图表使用的连续时间序列')
    expect(wrapper.text()).not.toContain('下单')
  })

  it.each([
    {
      name: 'partial',
      response: {
        ...marketBreadthFixture,
        validity: 'partial',
        b50: {
          ...marketBreadthFixture.b50,
          validity: 'partial',
          value: 0.45,
          coverage: { eligible: 503, observed: 460, ratio: 460 / 503 },
        },
      },
      expected: ['部分可用', '45.0%', '460 / 503'],
    },
    {
      name: 'insufficient coverage',
      response: {
        ...marketBreadthFixture,
        validity: 'insufficient_coverage',
        b50: {
          ...marketBreadthFixture.b50,
          validity: 'insufficient_coverage',
          value: null,
          coverage: { eligible: 503, observed: 400, ratio: 400 / 503 },
        },
      },
      expected: ['覆盖不足', '400 / 503', '不会使用零值或其他价格序列补齐'],
    },
    {
      name: 'stale',
      response: {
        ...marketBreadthFixture,
        validity: 'stale',
        freshness: { membership_age_days: 8, stale_after_days: 7 },
      },
      expected: ['已陈旧', '8 天 / 阈值 7 天', '超过 7 个日历日未更新'],
    },
  ])('宽度 $name 状态保留原始覆盖与告警', async ({ response, expected }) => {
    installApiMock({ '/api/market-radar/breadth': response })
    const { wrapper } = await mountPage(MarketRadarPage, '/market-radar?view=overview')
    await flushPromises()

    for (const text of expected) {
      expect(wrapper.text()).toContain(text)
    }
  })

  it('宽度缺失不影响价格模块', async () => {
    installApiMock({
      '/api/market-radar/breadth': {
        ...marketBreadthFixture,
        source_state: 'missing',
        validity: 'unavailable',
        as_of_date: null,
        calculated_at_utc: null,
        membership_date: null,
        membership_source: null,
        freshness: null,
        b50: null,
        b200: null,
        ad10: null,
        nhnl: null,
      },
    })
    const { wrapper } = await mountPage(MarketRadarPage, '/market-radar?view=overview')
    await flushPromises()

    expect(wrapper.text()).toContain('SPY · 20 日收益')
    expect(wrapper.text()).toContain('当前宽度快照不可用')
    expect(wrapper.text()).toContain('页面不会下载持仓、扫描 Catalog 或临时计算指标')
  })

  it('板块 URL 可恢复选择并通过详情接口打开抽屉', async () => {
    const fetchMock = installApiMock()
    const { wrapper } = await mountPage(
      MarketRadarPage,
      '/market-radar?view=sectors&sector=information_technology',
    )
    await flushPromises()

    expect(wrapper.text()).toContain('相对 SPY 强弱')
    expect(wrapper.text()).toContain('信息技术')
    expect(wrapper.text()).toContain('XLK.US')
    expect(wrapper.text()).toContain('板块代理 ETF')
    expect(wrapper.text()).toContain('宽度与 EPS 修正尚不可用')
    expect(
      fetchMock.mock.calls.some((call) => {
        const request = call[0]
        return (
          request instanceof Request &&
          new URL(request.url).pathname === '/api/market-radar/sectors/information_technology'
        )
      }),
    ).toBe(true)
  })

  it('个股筛选排序分页和详情都由 URL 与只读 API 驱动', async () => {
    const fetchMock = installApiMock()
    const { wrapper, router } = await mountPage(
      MarketRadarPage,
      '/market-radar?view=stocks&sector=information_technology&query=AAPL&sort=momentum&direction=desc&instrument=AAPL.US',
    )
    await flushPromises()

    expect(wrapper.text()).toContain('当前只具备价格趋势与风险维度')
    expect(wrapper.text()).toContain('AAPL')
    expect(wrapper.text()).toContain('126–21 动量')
    expect(wrapper.text()).toContain('修正、质量与估值尚不可用')
    expect(wrapper.text()).not.toContain('综合买入分')
    const stocksRequest = fetchMock.mock.calls
      .map((call) => call[0])
      .find(
        (request) =>
          request instanceof Request &&
          new URL(request.url).pathname === '/api/market-radar/stocks',
      )
    expect(stocksRequest).toBeInstanceOf(Request)
    if (stocksRequest instanceof Request) {
      const url = new URL(stocksRequest.url)
      expect(url.searchParams.get('sector')).toBe('information_technology')
      expect(url.searchParams.get('query')).toBe('AAPL')
      expect(url.searchParams.get('sort')).toBe('momentum')
      expect(url.searchParams.get('direction')).toBe('desc')
    }

    await wrapper.get('button.clear-filter').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query).toEqual({ view: 'stocks' })
  })

  it('数据库缺失时显示来源边界而不伪造指标', async () => {
    installApiMock({
      '/api/market-radar/summary': {
        ...marketRadarSummaryFixture,
        source_state: 'missing',
        as_of_date: null,
        calculated_at_utc: null,
        coverage: null,
        market: null,
      },
    })
    const { wrapper } = await mountPage(MarketRadarPage, '/market-radar')
    await flushPromises()

    expect(wrapper.text()).toContain('完整价格快照不可用')
    expect(wrapper.text()).toContain('页面不会读取 Catalog 或临时计算指标')
    expect(wrapper.text()).not.toContain('SPY · 20 日收益')
    expect(wrapper.text()).toContain('当前市场宽度')
    expect(wrapper.text()).toContain('48.11%')
  })
})

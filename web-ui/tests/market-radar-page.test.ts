import { flushPromises } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import MarketRadarPage from '../src/pages/MarketRadarPage.vue'
import {
  installApiMock,
  marketBreadthFixture,
  marketEarningsFixture,
  marketMacroFixture,
  marketRadarSummaryFixture,
} from './fixtures'
import { mountPage } from './helpers'

describe('市场雷达页面', () => {
  it('总览展示真实价格、宏观、宽度和盈利修正快照', async () => {
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
    expect(wrapper.text()).toContain('实际利率 × 风险偏好')
    expect(wrapper.text()).toContain('宽松型 Risk-on')
    expect(wrapper.text()).toContain('FRED · DFII10')
    expect(wrapper.text()).toContain('当前修订')
    expect(wrapper.text()).toContain('HYG/LQD ETF 代理')
    expect(wrapper.get('[role="img"]').attributes('aria-label')).toContain('共 3 个轨迹点')
    expect(wrapper.text()).toContain('盈利预期脉冲')
    expect(wrapper.text()).toContain('EODHD Calendar Trends')
    expect(wrapper.text()).toContain('当前市场')
    expect(wrapper.text()).toContain('策略 Watchlist')
    expect(wrapper.text()).toContain('501 / 503')
    expect(wrapper.text()).toContain('10 / 10')
    expect(wrapper.text()).toContain('1 个成员未分类')
    expect(wrapper.text()).toContain('不把采集历史解释为 PIT 分析师预期序列')
    expect(wrapper.text()).not.toContain('下单')
  })

  it('盈利快照陈旧时保留原始聚合并显示当前性告警', async () => {
    installApiMock({
      '/api/market-radar/earnings': {
        ...marketEarningsFixture,
        validity: 'stale',
        freshness: { snapshot_age_days: 4, stale_after_days: 3 },
      },
    })
    const { wrapper } = await mountPage(MarketRadarPage, '/market-radar?view=overview')
    await flushPromises()

    expect(wrapper.text()).toContain('已陈旧')
    expect(wrapper.text()).toContain('4 天 / 阈值 3 天')
    expect(wrapper.text()).toContain('超过 3 个日历日未更新')
    expect(wrapper.text()).toContain('501 / 503')
  })

  it('盈利快照缺失不影响价格、宏观和宽度', async () => {
    installApiMock({
      '/api/market-radar/earnings': {
        ...marketEarningsFixture,
        source_state: 'missing',
        validity: 'unavailable',
        as_of_date: null,
        calculated_at_utc: null,
        source: null,
        freshness: null,
        membership: null,
        watchlist: null,
        market: null,
        sectors: [],
      },
    })
    const { wrapper } = await mountPage(MarketRadarPage, '/market-radar?view=overview')
    await flushPromises()

    expect(wrapper.text()).toContain('SPY · 20 日收益')
    expect(wrapper.text()).toContain('宽松型 Risk-on')
    expect(wrapper.text()).toContain('48.11%')
    expect(wrapper.text()).toContain('盈利修正快照不可用')
    expect(wrapper.text()).toContain('页面不会连接 EODHD')
  })

  it.each([
    {
      name: 'stale',
      response: {
        ...marketMacroFixture,
        validity: 'stale',
        freshness: { risk_appetite_age_days: 4, real_rate_age_days: 5, stale_after_days: 3 },
      },
      expected: ['已陈旧', '利率 5 天 · 风险 4 天', '超过 3 个日历日未更新'],
    },
    {
      name: 'insufficient history',
      response: {
        ...marketMacroFixture,
        validity: 'insufficient_history',
        current: null,
        trajectory: [],
        duration_observations: 0,
        real_rate: {
          ...marketMacroFixture.real_rate,
          validity: 'insufficient_history',
          pressure_z: null,
          observations: 300,
        },
      },
      expected: ['历史不足', '暂不能绘制宏观象限', '300 / 504 个变化观测'],
    },
  ])('宏观 $name 状态不被前端改写', async ({ response, expected }) => {
    installApiMock({ '/api/market-radar/macro': response })
    const { wrapper } = await mountPage(MarketRadarPage, '/market-radar?view=overview')
    await flushPromises()

    for (const text of expected) {
      expect(wrapper.text()).toContain(text)
    }
  })

  it('宏观快照缺失不影响价格和宽度', async () => {
    installApiMock({
      '/api/market-radar/macro': {
        ...marketMacroFixture,
        source_state: 'missing',
        validity: 'unavailable',
        as_of_date: null,
        calculated_at_utc: null,
        freshness: null,
        neutral_band: null,
        alignment_max_age_days: null,
        real_rate_source: null,
        real_rate_vintage: null,
        credit_source: null,
        price_source: null,
        real_rate: null,
        risk_appetite: null,
        current: null,
        trajectory: [],
        duration_observations: 0,
      },
    })
    const { wrapper } = await mountPage(MarketRadarPage, '/market-radar?view=overview')
    await flushPromises()

    expect(wrapper.text()).toContain('SPY · 20 日收益')
    expect(wrapper.text()).toContain('48.11%')
    expect(wrapper.text()).toContain('宏观象限快照不可用')
    expect(wrapper.text()).toContain('页面不会连接 FRED')
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

    expect(wrapper.text()).toContain('价格强弱 × 盈利修正')
    expect(wrapper.text()).toContain('信息技术')
    expect(wrapper.text()).toContain('XLK.US')
    expect(wrapper.text()).toContain('板块代理 ETF')
    expect(wrapper.text()).toContain('EPS 修正宽度')
    expect(wrapper.text()).toContain('30.56%')
    expect(wrapper.text()).toContain('72 / 72')
    expect(wrapper.text()).toContain('当前成员行业分类聚合')
    expect(wrapper.text()).toContain('板块历史宽度尚不可用')
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

    expect(wrapper.text()).toContain('当前个股接口只具备价格趋势与风险维度')
    expect(wrapper.text()).toContain('AAPL')
    expect(wrapper.text()).toContain('126–21 动量')
    expect(wrapper.text()).toContain('个股修正、质量与估值尚不可用')
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

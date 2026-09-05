import { flushPromises } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'

import MarketRadarPage from '../src/pages/MarketRadarPage.vue'
import {
  installApiMock,
  marketBreadthFixture,
  marketEarningsFixture,
  marketEventsFixture,
  marketFundamentalsFixture,
  marketMacroFixture,
  marketRadarSummaryFixture,
  marketRadarStocksFixture,
} from './fixtures'
import { mountPage } from './helpers'

describe('市场雷达页面', () => {
  it.each(['missing', 'empty', 'error'] as const)(
    '板块盈利首次 %s 不影响价格且无旧数值',
    async (state) => {
      const fetchMock = installApiMock({
        '/api/market-radar/earnings': {
          ...marketEarningsFixture,
          source_state: state === 'error' ? 'available' : state,
          as_of_date: null,
          freshness: null,
          market: null,
          watchlist: null,
          membership: null,
          sectors: [],
        },
      })
      if (state === 'error') {
        const normal = fetchMock.getMockImplementation()
        if (!normal) throw new Error('缺少 API fixture')
        fetchMock.mockImplementation((input, init) => {
          const request = input instanceof Request ? input : new Request(input, init)
          if (new URL(request.url).pathname.endsWith('/earnings'))
            return Promise.resolve(
              new Response(
                JSON.stringify({
                  code: 'source_unavailable',
                  title: 'Unavailable',
                  detail: '合成首次失败',
                }),
                { status: 503, headers: { 'Content-Type': 'application/problem+json' } },
              ),
            )
          return normal(input, init)
        })
      }
      const { wrapper } = await mountPage(MarketRadarPage, '/market-radar?view=sectors')
      await flushPromises()
      expect(wrapper.get('.sector-earnings-source').text()).toContain(
        state === 'error' ? '板块盈利读取失败' : '板块盈利快照不可用',
      )
      expect(wrapper.get('tbody').text()).toContain('XLK.US')
      expect(
        wrapper.findAll('.earnings-table-cell strong').every((cell) => cell.text() === '—'),
      ).toBe(true)
      wrapper.unmount()
    },
  )

  it('基本面深链的原入口不存在时，关闭回到当前视图', async () => {
    installApiMock()
    const { wrapper } = await mountPage(
      MarketRadarPage,
      '/market-radar?view=stocks&dimension=fundamentals&instrument=JPM.US',
    )
    await flushPromises()
    await wrapper.get('[aria-label="关闭详情"]').trigger('click')
    await flushPromises()
    expect(document.activeElement).toBe(
      wrapper.get('[aria-label="市场雷达视图"] [aria-selected="true"]').element,
    )
    wrapper.unmount()
  })

  it('价格缺失仍显示六项真实摘要，导航不使用含义混杂的计数', async () => {
    installApiMock({
      '/api/market-radar/summary': {
        ...marketRadarSummaryFixture,
        source_state: 'empty',
        as_of_date: null,
        calculated_at_utc: null,
        coverage: null,
        market: null,
      },
    })
    const { wrapper } = await mountPage(MarketRadarPage, '/market-radar')
    await flushPromises()
    expect(wrapper.findAll('.module-card')).toHaveLength(6)
    expect(wrapper.find('.price-summary').exists()).toBe(false)
    expect(wrapper.text()).toContain('完整价格快照不可用')
    expect(wrapper.text()).toContain('当前市场宽度')
    expect(wrapper.findAll('[aria-label="市场雷达视图"] button').map((tab) => tab.text())).toEqual([
      '总览',
      '板块',
      '个股',
    ])
    wrapper.unmount()
  })

  it.each([
    ['summary', '', '.snapshot-strip'],
    ['macro', '', '.macro-meta'],
    ['breadth', '', '.breadth-meta'],
    ['earnings', '?view=sectors', '.earnings-source-meta'],
    ['fundamentals', '?view=stocks&dimension=fundamentals', '.fundamentals-meta'],
  ])('%s 重读期间有提示，失败不暴露旧值，恢复后重新展示', async (source, query, selector) => {
    const fetchMock = installApiMock()
    const normal = fetchMock.getMockImplementation()
    if (!normal) throw new Error('缺少 API fixture')
    let failure = false
    let release: (() => void) | undefined
    fetchMock.mockImplementation(async (input, init) => {
      const request = input instanceof Request ? input : new Request(input, init)
      if (failure && new URL(request.url).pathname === `/api/market-radar/${source}`) {
        await new Promise<void>((resolve) => {
          release = resolve
        })
        return new Response(
          JSON.stringify({
            code: 'source_unavailable',
            title: 'Unavailable',
            detail: '合成读取失败',
          }),
          { status: 503, headers: { 'Content-Type': 'application/problem+json' } },
        )
      }
      return normal(input, init)
    })
    const { wrapper, queryClient } = await mountPage(MarketRadarPage, `/market-radar${query}`)
    await flushPromises()
    expect(wrapper.find(selector).exists()).toBe(true)
    failure = true
    const refresh = queryClient.refetchQueries({ queryKey: ['market-radar', source] })
    await flushPromises()
    expect(wrapper.get('.radar-read-status').text()).toContain('正在重新读取')
    expect(wrapper.find(selector).exists()).toBe(true)
    if (!release) throw new Error('未捕获重读请求')
    release()
    await refresh
    await flushPromises()
    expect(wrapper.text()).toContain('合成读取失败')
    expect(wrapper.find(selector).exists()).toBe(false)
    if (source === 'earnings') {
      expect(wrapper.get('tbody').text()).toContain('XLK.US')
      expect(wrapper.get('tbody').text()).not.toContain('30.56%')
      expect(
        wrapper.findAll('.earnings-table-cell strong').every((cell) => cell.text() === '—'),
      ).toBe(true)
    }
    if (source === 'summary') {
      expect(wrapper.find('.module-grid').exists()).toBe(false)
      expect(wrapper.find('.events-panel').exists()).toBe(true)
    }
    failure = false
    await queryClient.refetchQueries({ queryKey: ['market-radar', source] })
    await flushPromises()
    expect(wrapper.find(selector).exists()).toBe(true)
    expect(wrapper.text()).not.toContain('合成读取失败')
    expect(
      fetchMock.mock.calls.every((call) => call[0] instanceof Request && call[0].method === 'GET'),
    ).toBe(true)
    wrapper.unmount()
  })

  it.each([404, 503])('板块价格详情 %s 不隐藏盈利，深链关闭回到视图入口', async (status) => {
    const fetchMock = installApiMock()
    const normal = fetchMock.getMockImplementation()
    if (!normal) throw new Error('缺少 API fixture')
    fetchMock.mockImplementation((input, init) => {
      const request = input instanceof Request ? input : new Request(input, init)
      return new URL(request.url).pathname.endsWith('/sectors/information_technology')
        ? Promise.resolve(
            new Response(
              JSON.stringify({
                code: 'source_unavailable',
                title: 'Unavailable',
                detail: '价格详情不可读',
              }),
              {
                status,
                headers: { 'Content-Type': 'application/problem+json' },
              },
            ),
          )
        : normal(input, init)
    })
    const { wrapper, router } = await mountPage(
      MarketRadarPage,
      '/market-radar?view=sectors&sector=information_technology',
    )
    await flushPromises()
    expect(document.body.classList.contains('drawer-open')).toBe(true)
    const drawer = wrapper.get('[role="dialog"]')
    expect(drawer.text()).toContain(status === 404 ? '该板块无价格快照' : '价格详情读取失败')
    expect(drawer.text()).toContain('30.56%')
    expect(drawer.text()).toContain('当前成员行业分类聚合')
    await drawer.get('[aria-label="关闭详情"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query).toEqual({ view: 'sectors' })
    expect(document.activeElement).toBe(
      wrapper.get('[aria-label="市场雷达视图"] [aria-selected="true"]').element,
    )
    expect(document.body.classList.contains('drawer-open')).toBe(false)
    wrapper.unmount()
  })

  it('合成两页价格记录验证分页边界，越界回首页保留筛选', async () => {
    const fetchMock = installApiMock()
    const normal = fetchMock.getMockImplementation()
    if (!normal) throw new Error('缺少 API fixture')
    const first = marketRadarStocksFixture.items[0]
    if (!first) throw new Error('缺少合成标的')
    const items = Array.from({ length: 21 }, (_, index) => ({
      ...first,
      instrument_id: `TEST${String(index)}.US`,
      symbol: `TEST${String(index)}`,
    }))
    fetchMock.mockImplementation((input, init) => {
      const request = input instanceof Request ? input : new Request(input, init)
      const url = new URL(request.url)
      if (url.pathname !== '/api/market-radar/stocks') return normal(input, init)
      const offset = Number(url.searchParams.get('offset'))
      const limit = Number(url.searchParams.get('limit'))
      return Promise.resolve(
        new Response(
          JSON.stringify({
            ...marketRadarStocksFixture,
            items: items.slice(offset, offset + limit),
            offset,
            limit,
            has_more: offset + limit < items.length,
          }),
        ),
      )
    })
    const { wrapper, router } = await mountPage(
      MarketRadarPage,
      '/market-radar?view=stocks&sector=information_technology&sort=momentum&direction=desc',
    )
    await flushPromises()
    expect(wrapper.findAll('tbody tr')).toHaveLength(20)
    expect(wrapper.get('[aria-label="上一页"]').attributes('disabled')).toBeDefined()
    await wrapper.get('[aria-label="下一页"]').trigger('click')
    await flushPromises()
    expect(wrapper.findAll('tbody tr')).toHaveLength(1)
    expect(wrapper.text()).toContain('第 21–21 条 · 第 2 页')
    expect(wrapper.get('[aria-label="下一页"]').attributes('disabled')).toBeDefined()
    await router.replace({ query: { ...router.currentRoute.value.query, page: '3' } })
    await flushPromises()
    expect(wrapper.text()).toContain('当前页没有记录')
    await wrapper.get('[aria-label="回到第一页"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query).toEqual({
      view: 'stocks',
      sector: 'information_technology',
      sort: 'momentum',
      direction: 'desc',
    })
    expect(wrapper.findAll('tbody tr')).toHaveLength(20)
    wrapper.unmount()
  })

  it('事件深链恢复筛选；切换详情、来源和日期不追加 GET，离开总览清理状态', async () => {
    const fetchMock = installApiMock()
    const { wrapper, router } = await mountPage(
      MarketRadarPage,
      '/market-radar?event_kind=earnings&event_date=2026-09-05',
    )
    await flushPromises()
    expect(wrapper.findAll('.events-panel .event-day')).toHaveLength(1)
    expect(wrapper.findAll('.events-panel .event-row')).toHaveLength(1)
    await wrapper.get('[aria-label="查看财报事件 AAPL.US"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[role="dialog"]').text()).toContain('币种未确认')
    await wrapper.get('[aria-label="关闭详情"]').trigger('click')
    expect(router.currentRoute.value.query).toEqual({
      event_kind: 'earnings',
      event_date: '2026-09-05',
    })
    await wrapper.get('[aria-label="事件来源筛选"] button:first-child').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query).toEqual({ event_date: '2026-09-05' })
    await wrapper.get('[aria-label="筛选 2026-09-18"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query.event_date).toBe('2026-09-18')
    const requests = () =>
      fetchMock.mock.calls
        .map((call) => call[0])
        .filter(
          (request): request is Request =>
            request instanceof Request &&
            new URL(request.url).pathname === '/api/market-radar/events',
        )
    expect(requests()).toHaveLength(1)
    await wrapper.get('[aria-label="查看财报事件 MSFT.US"]').trigger('click')
    await router.push('/market-radar?view=stocks')
    await flushPromises()
    expect(wrapper.find('.events-panel').exists()).toBe(false)
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
    expect(router.currentRoute.value.query).toEqual({ view: 'stocks' })
    expect(requests()).toHaveLength(1)
    expect(
      fetchMock.mock.calls.every((call) => call[0] instanceof Request && call[0].method === 'GET'),
    ).toBe(true)
    wrapper.unmount()
  })

  it.each(['event_kind=bad&event_date=2026-09-99', 'event_kind=economic&event_date=2025-09-05'])(
    '非法事件筛选按默认处理并在下次操作清理：%s',
    async (query) => {
      installApiMock()
      const { wrapper, router } = await mountPage(MarketRadarPage, `/market-radar?${query}`)
      await flushPromises()
      expect(wrapper.findAll('.events-panel .event-day')).toHaveLength(14)
      await wrapper.get('[aria-label="事件来源筛选"] button:first-child').trigger('click')
      await flushPromises()
      expect(router.currentRoute.value.query).toEqual({})
      wrapper.unmount()
    },
  )

  it('个股视图不启用事件查询，总览事件不依赖其他模块', async () => {
    const fetchMock = installApiMock()
    const normal = fetchMock.getMockImplementation()
    if (!normal) throw new Error('缺少默认 API fixture')
    fetchMock.mockImplementation((input, init) => {
      const request = input instanceof Request ? input : new Request(input, init)
      const path = new URL(request.url).pathname
      if (
        ['summary', 'earnings', 'macro', 'breadth'].some(
          (part) => path === `/api/market-radar/${part}`,
        )
      )
        return Promise.resolve(new Response('{}', { status: 503 }))
      return normal(input, init)
    })
    const { wrapper, router } = await mountPage(MarketRadarPage, '/market-radar?view=stocks')
    await flushPromises()
    expect(
      fetchMock.mock.calls.some(
        (call) => call[0] instanceof Request && new URL(call[0].url).pathname.endsWith('/events'),
      ),
    ).toBe(false)
    await router.push('/market-radar')
    await flushPromises()
    expect(wrapper.get('.events-panel').text()).toContain('Synthetic Price Index')
    wrapper.unmount()
  })

  it('事件重读失败不展示旧缓存，恢复后只按新响应展示', async () => {
    const fetchMock = installApiMock()
    const normal = fetchMock.getMockImplementation()
    if (!normal) throw new Error('缺少默认 API fixture')
    let fail = false
    fetchMock.mockImplementation((input, init) => {
      const request = input instanceof Request ? input : new Request(input, init)
      if (fail && new URL(request.url).pathname.endsWith('/events'))
        return Promise.resolve(new Response('{}', { status: 503 }))
      return normal(input, init)
    })
    const { wrapper, queryClient } = await mountPage(MarketRadarPage, '/market-radar')
    await flushPromises()
    expect(wrapper.findAll('.events-panel .event-row')).toHaveLength(4)
    fail = true
    await wrapper.get('.events-reload').trigger('click')
    await flushPromises()
    expect(wrapper.get('.events-panel').text()).toContain('事件读取失败')
    expect(wrapper.findAll('.events-panel .event-row')).toHaveLength(0)
    expect(queryClient.getQueryData(['market-radar', 'events'])).toEqual(marketEventsFixture)
    fail = false
    await wrapper.get('.events-panel .data-state--error button').trigger('click')
    await flushPromises()
    expect(wrapper.findAll('.events-panel .event-row')).toHaveLength(4)
    wrapper.unmount()
  })

  it('基本面深链恢复整批列表和同一抽屉，切换维度清除价格参数', async () => {
    const fetchMock = installApiMock()
    const { wrapper, router } = await mountPage(
      MarketRadarPage,
      '/market-radar?view=stocks&dimension=fundamentals&instrument=JPM.US&sector=energy&query=XOM&sort=momentum&direction=desc&page=3',
    )
    await flushPromises()
    expect(wrapper.find('.stock-filters').exists()).toBe(false)
    expect(wrapper.findAll('.fundamentals-row')).toHaveLength(4)
    const drawer = wrapper.get('[role="dialog"]')
    expect(drawer.text()).toContain('该标的无价格快照')
    expect(drawer.text()).toContain('金融企业')
    expect(drawer.text()).toContain('20.0%')
    expect(drawer.findAll('.fundamental-metric')).toHaveLength(7)
    expect(drawer.text()).toContain('不适用于该公司类型')
    expect(wrapper.text()).toContain('价格数据日期')
    expect(wrapper.text()).toContain('基本面采集日 · UTC')
    const requests = fetchMock.mock.calls
      .map((call) => call[0])
      .filter((input): input is Request => input instanceof Request)
    expect(
      requests.filter((request) => new URL(request.url).pathname.endsWith('/fundamentals')),
    ).toHaveLength(1)
    expect(requests.every((request) => request.method === 'GET')).toBe(true)
    await wrapper.get('[aria-label="个股观察维度"] [role="tab"]:first-child').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query).toEqual({ view: 'stocks' })
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
    expect(wrapper.find('.stock-filters').exists()).toBe(true)
    await wrapper.get('[aria-label="个股观察维度"] [role="tab"]:last-child').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query).toEqual({ view: 'stocks', dimension: 'fundamentals' })
  })

  it('打开与关闭基本面抽屉保留维度且不清空整批列表', async () => {
    installApiMock()
    const { wrapper, router } = await mountPage(
      MarketRadarPage,
      '/market-radar?view=stocks&dimension=fundamentals',
    )
    await flushPromises()
    await wrapper.get('[aria-label="查看 AAPL.US 基本面详情"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query.instrument).toBe('AAPL.US')
    expect(wrapper.get('[role="dialog"]').text()).toContain('价格配置板块')
    expect(wrapper.get('[role="dialog"]').text()).toContain('来源行业 / 分类映射')
    await wrapper.get('[role="dialog"] button[aria-label="关闭详情"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query).toEqual({ view: 'stocks', dimension: 'fundamentals' })
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
    expect(wrapper.findAll('.fundamentals-row')).toHaveLength(4)
  })

  it.each(['fundamentals', 'stocks/AAPL.US'] as const)(
    '%s 失败不屏蔽另一个详情来源，重试仅 GET',
    async (failedPath) => {
      const fetchMock = installApiMock()
      const normal = fetchMock.getMockImplementation()
      if (!normal) throw new Error('缺少默认 API fixture')
      let fail = true
      fetchMock.mockImplementation((input, init) => {
        const request = input instanceof Request ? input : new Request(input, init)
        if (fail && new URL(request.url).pathname === `/api/market-radar/${failedPath}`) {
          return Promise.resolve(
            new Response(
              JSON.stringify({ code: 'source_unavailable', detail: '只读来源无法安全读取。' }),
              {
                status: 503,
                headers: { 'Content-Type': 'application/problem+json' },
              },
            ),
          )
        }
        return normal(input, init)
      })
      const { wrapper } = await mountPage(
        MarketRadarPage,
        '/market-radar?view=stocks&dimension=fundamentals&instrument=AAPL.US',
      )
      await flushPromises()
      const drawer = wrapper.get('[role="dialog"]')
      expect(drawer.text()).toContain(
        failedPath === 'fundamentals' ? '基本面详情读取失败' : '价格详情读取失败',
      )
      expect(drawer.text()).toContain(failedPath === 'fundamentals' ? '126–21 动量' : '-12.0%')
      expect(drawer.text()).not.toContain('当前已发布快照中不存在该标的')
      fail = false
      await drawer.get('.data-state--error button').trigger('click')
      await flushPromises()
      expect(drawer.text()).not.toContain('读取失败')
      await vi.waitFor(() => {
        expect(wrapper.get('[role="dialog"]').text()).toContain('126–21 动量')
        expect(wrapper.get('[role="dialog"]').text()).toContain('-12.0%')
      })
      expect(
        fetchMock.mock.calls.every(
          (call) => call[0] instanceof Request && call[0].method === 'GET',
        ),
      ).toBe(true)
    },
  )

  it('基本面尚在加载时价格详情已经可用', async () => {
    const fetchMock = installApiMock()
    const normal = fetchMock.getMockImplementation()
    if (!normal) throw new Error('缺少默认 API fixture')
    let resolveFundamentals: (response: Response) => void = () => {
      throw new Error('尚未请求')
    }
    fetchMock.mockImplementation((input, init) => {
      const request = input instanceof Request ? input : new Request(input, init)
      if (new URL(request.url).pathname.endsWith('/fundamentals')) {
        return new Promise<Response>((resolve) => {
          resolveFundamentals = resolve
        })
      }
      return normal(input, init)
    })
    const { wrapper } = await mountPage(
      MarketRadarPage,
      '/market-radar?view=stocks&instrument=AAPL.US',
    )
    await flushPromises()
    expect(wrapper.get('[role="dialog"]').text()).toContain('126–21 动量')
    expect(wrapper.get('[role="dialog"]').text()).toContain('正在读取基本面详情')
    resolveFundamentals(
      new Response(JSON.stringify(marketFundamentalsFixture), {
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    await flushPromises()
    expect(wrapper.get('[role="dialog"]').text()).toContain('-12.0%')
  })

  it.each(['AAPL.US', 'AAPL.NASDAQ'])(
    '按完整 ID 查找 %s，不猜测跨 venue 身份',
    async (instrument) => {
      installApiMock({
        '/api/market-radar/fundamentals': {
          ...marketFundamentalsFixture,
          items: marketFundamentalsFixture.items.filter((item) => item.instrument_id !== 'AAPL.US'),
        },
      })
      const { wrapper } = await mountPage(
        MarketRadarPage,
        `/market-radar?view=stocks&dimension=fundamentals&instrument=${instrument}`,
      )
      await flushPromises()
      const drawer = wrapper.get('[role="dialog"]')
      expect(drawer.text()).toContain('该标的无基本面快照')
      if (instrument === 'AAPL.US') {
        expect(drawer.text()).toContain('126–21 动量')
        expect(drawer.text()).not.toContain('当前已发布快照中不存在该标的')
      } else {
        expect(drawer.text()).toContain('当前已发布快照中不存在该标的')
      }
    },
  )

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
    expect(wrapper.text()).toContain('观察股 Watchlist')
    expect(wrapper.text()).not.toContain('配置中的 10 只')
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

    expect(wrapper.text()).toContain('当前展示价格趋势与风险')
    expect(wrapper.text()).toContain('AAPL')
    expect(wrapper.text()).toContain('126–21 动量')
    expect(wrapper.text()).toContain('个股 EPS 修正、ROIC 与同行分位尚不可用')
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

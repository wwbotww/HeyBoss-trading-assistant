import { flushPromises } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import PortfolioPage from '../src/pages/PortfolioPage.vue'
import { installApiMock, portfolioFixture } from './fixtures'
import { mountPage } from './helpers'

describe('账户与持仓', () => {
  it.each([
    { is_stale: true },
    { broker_connected: false },
    { reconciliation_complete: false },
    { account_updated_at_utc: null },
    { source_state: 'missing' },
  ])('不将未确认空列表展示为无持仓：%j', async (quality) => {
    installApiMock({ '/api/portfolio': { ...portfolioFixture, positions: [], ...quality } })
    const { wrapper } = await mountPage(PortfolioPage, '/portfolio')
    await flushPromises()
    expect(wrapper.text()).toContain('持仓尚未确认')
    expect(wrapper.text()).toContain('待确认')
    expect(wrapper.text()).toContain('快照时券商连接')
    expect(wrapper.text()).not.toContain('当前没有持仓')
  })

  it('完整新鲜核对后的空列表可以确认空仓', async () => {
    installApiMock({ '/api/portfolio': { ...portfolioFixture, positions: [] } })
    const { wrapper } = await mountPage(PortfolioPage, '/portfolio')
    await flushPromises()
    expect(wrapper.text()).toContain('当前没有持仓')
    expect(wrapper.text()).not.toContain('持仓尚未确认')
    expect(wrapper.text()).not.toContain('快照时券商连接')
  })

  it('过期非空仓位标记历史来源并保留记录', async () => {
    installApiMock({ '/api/portfolio': { ...portfolioFixture, is_stale: true } })
    const { wrapper } = await mountPage(PortfolioPage, '/portfolio')
    await flushPromises()
    expect(wrapper.text()).toContain('以下为历史持仓快照')
    expect(wrapper.text()).toContain('采样于')
    expect(wrapper.text()).toContain('当前持仓尚未确认')
    expect(wrapper.text()).toContain('AAPL.US')
  })

  it('区分持仓 EOD 估值与账户资金历史', async () => {
    installApiMock()
    const { wrapper, router } = await mountPage(PortfolioPage, '/portfolio')
    await flushPromises()

    expect(wrapper.text()).toContain('$125,430.25')
    expect(wrapper.text()).toContain('AAPL.US')
    expect(wrapper.text()).toContain('EOD 参考价')
    expect(wrapper.text()).toContain('不是 IBKR 实时行情')
    expect(wrapper.text()).toContain('可用资金')
    expect(wrapper.text()).toContain('现金余额')
    expect(wrapper.text()).not.toContain('现金合计')
    expect(wrapper.text()).toContain('券商最近更新')
    expect(wrapper.text()).toContain('已连接')

    const historyTab = wrapper
      .findAll('button')
      .find((button) => button.text().includes('资金快照'))
    if (!historyTab) {
      throw new Error('资金快照标签页不存在')
    }
    await historyTab.trigger('click')
    await flushPromises()

    expect(router.currentRoute.value.query.tab).toBe('history')
    expect(wrapper.text()).toContain('最近资金快照')
    expect(wrapper.text()).toContain('DU***42')
    expect(wrapper.text()).toContain('净清算价值')
    expect(wrapper.get('[role="img"]').attributes('aria-label')).toContain('账户净清算价值历史')
  })

  it('保留断连、未核对与未知券商时间', async () => {
    installApiMock({
      '/api/portfolio': {
        ...portfolioFixture,
        broker_connected: false,
        reconciliation_complete: false,
        account_updated_at_utc: null,
        is_stale: true,
        not_ready_reason: 'broker disconnected or connection unknown',
      },
    })
    const { wrapper } = await mountPage(PortfolioPage, '/portfolio')
    await flushPromises()
    expect(wrapper.text()).toContain('已断开')
    expect(wrapper.text()).toContain('未确认')
    expect(wrapper.text()).toContain('broker disconnected or connection unknown')
  })
})

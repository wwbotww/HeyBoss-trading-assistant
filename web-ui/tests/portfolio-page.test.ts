import { flushPromises } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import PortfolioPage from '../src/pages/PortfolioPage.vue'
import { installApiMock } from './fixtures'
import { mountPage } from './helpers'

describe('账户与持仓', () => {
  it('区分持仓 EOD 估值与账户资金历史', async () => {
    installApiMock()
    const { wrapper, router } = await mountPage(PortfolioPage, '/portfolio')
    await flushPromises()

    expect(wrapper.text()).toContain('$125,430.25')
    expect(wrapper.text()).toContain('AAPL.US')
    expect(wrapper.text()).toContain('EOD 参考价')
    expect(wrapper.text()).toContain('不是 IBKR 实时行情')

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
})

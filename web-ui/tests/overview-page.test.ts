import { flushPromises } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import OverviewPage from '../src/pages/OverviewPage.vue'
import { installApiMock, overviewFixture } from './fixtures'
import { mountPage } from './helpers'

describe('操作总览', () => {
  it.each([false, true])('未核对的持仓数量显示待确认，过期=%s', async (is_stale) => {
    installApiMock({
      '/api/overview': {
        ...overviewFixture,
        portfolio: {
          ...overviewFixture.portfolio,
          positions: [],
          reconciliation_complete: false,
          is_stale,
        },
      },
    })
    const { wrapper } = await mountPage(OverviewPage)
    await flushPromises()
    const metric = wrapper.findAll('.metric-card').find((item) => item.text().includes('当前持仓'))
    expect(metric?.text()).toContain('待确认')
    expect(metric?.text()).toContain('历史快照')
  })

  it('展示账户、工作流与策略事实', async () => {
    installApiMock()
    const { wrapper } = await mountPage(OverviewPage)
    await flushPromises()

    expect(wrapper.text()).toContain('$125,430.25')
    expect(wrapper.text()).toContain('IBKR Paper')
    expect(wrapper.text()).toContain('PatchTST E3')
    expect(wrapper.text()).toContain('待处理')
    expect(wrapper.text()).toContain('数据质量')
  })

  it('账户源缺失时不伪造资产数据', async () => {
    installApiMock({
      '/api/overview': {
        ...overviewFixture,
        portfolio: {
          ...overviewFixture.portfolio,
          source_state: 'missing',
          net_liquidation: null,
          available_funds: null,
          total_cash_value: null,
          positions: [],
        },
      },
    })
    const { wrapper } = await mountPage(OverviewPage)
    await flushPromises()

    expect(wrapper.text()).toContain('账户快照不可用')
    expect(wrapper.text()).toContain('—')
  })
})

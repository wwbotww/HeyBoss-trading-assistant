import { flushPromises } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import BacktestsPage from '../src/pages/BacktestsPage.vue'
import { equityReportFixture, installApiMock } from './fixtures'
import { mountPage } from './helpers'

describe('回测中心', () => {
  it('从 URL 恢复运行并展示绩效、权益和固定报告', async () => {
    installApiMock()
    const { wrapper, router } = await mountPage(BacktestsPage, '/backtests?run=web-run')
    await flushPromises()

    expect(router.currentRoute.value.query.run).toBe('web-run')
    expect(wrapper.text()).toContain('$105,000.00')
    expect(wrapper.text()).toContain('10.0%')
    expect(wrapper.get('[role="img"]').attributes('aria-label')).toContain('回测权益曲线')
    expect(wrapper.text()).toContain('timestamp_utc')

    const ordersTab = wrapper.findAll('[role="tab"]').find((tab) => tab.text().includes('订单'))
    if (!ordersTab) {
      throw new Error('订单报告标签不存在')
    }
    await ordersTab.trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query.report).toBe('orders')
    expect(wrapper.text()).toContain('value')
  })

  it('权益报告缺少确定字段时显示损坏状态', async () => {
    installApiMock({
      '/api/backtests/web-run/equity': {
        ...equityReportFixture,
        columns: ['date', 'balance'],
      },
    })
    const { wrapper } = await mountPage(BacktestsPage, '/backtests?run=web-run')
    await flushPromises()

    expect(wrapper.text()).toContain('权益图无法生成')
    expect(wrapper.text()).toContain('缺少 timestamp_utc 或 equity 列')
  })
})

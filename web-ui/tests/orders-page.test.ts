import { flushPromises } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import OrdersPage from '../src/pages/OrdersPage.vue'
import { installApiMock } from './fixtures'
import { mountPage } from './helpers'

describe('订单与成交', () => {
  it('分别展示订单生命周期和逐笔成交', async () => {
    installApiMock()
    const { wrapper, router } = await mountPage(OrdersPage, '/orders')
    await flushPromises()

    expect(wrapper.text()).toContain('O-20260815-001')
    expect(wrapper.text()).toContain('已成交')
    expect(wrapper.text()).toContain('这里不是下单终端')

    await wrapper.get('.row-trigger').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query.order).toBe('O-20260815-001')
    expect(wrapper.text()).toContain('订单事件')
    expect(wrapper.text()).toContain('佣金 $1.00')

    await wrapper.get('button[aria-label="关闭详情"]').trigger('click')

    const fillsTab = wrapper.findAll('button').find((button) => button.text().includes('逐笔成交'))
    if (!fillsTab) {
      throw new Error('逐笔成交标签页不存在')
    }
    await fillsTab.trigger('click')
    await flushPromises()

    expect(router.currentRoute.value.query.tab).toBe('fills')
    expect(wrapper.text()).toContain('T-20260815-001')
    expect(wrapper.text()).toContain('$205.25')
    expect(wrapper.text()).toContain('逐笔记录')
  })
})

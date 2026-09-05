import { flushPromises } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import AppShell from '../src/components/AppShell.vue'
import { installApiMock } from './fixtures'
import { mountPage } from './helpers'

describe('应用壳', () => {
  it('提供八个真实入口、只读上下文和全局刷新', async () => {
    const fetchMock = installApiMock()
    const { wrapper } = await mountPage(AppShell)
    await flushPromises()

    expect(wrapper.text()).toContain('HeyBoss')
    expect(wrapper.text()).toContain('操作总览')
    expect(wrapper.text()).toContain('账户与持仓')
    expect(wrapper.text()).toContain('决策流')
    expect(wrapper.text()).toContain('订单与成交')
    expect(wrapper.text()).toContain('策略与因子')
    expect(wrapper.text()).toContain('市场雷达')
    expect(wrapper.text()).toContain('回测中心')
    expect(wrapper.text()).toContain('数据与系统')
    expect(wrapper.text()).toContain('Paper · 只读')
    expect(wrapper.text()).toContain('API 正常')

    await wrapper.get('button[aria-label="刷新全部只读数据"]').trigger('click')
    await flushPromises()
    expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(2)
  })

  it('移动端菜单包含完整分组导航', async () => {
    installApiMock()
    const { wrapper, router } = await mountPage(AppShell)
    await flushPromises()

    const opener = wrapper.get<HTMLButtonElement>('button[aria-label="打开主导航"]')
    opener.element.focus()
    await opener.trigger('click')
    expect(wrapper.text()).toContain('研究与系统')
    expect(wrapper.text()).toContain('页面不形成第二条执行路径')
    expect(document.body.classList.contains('drawer-open')).toBe(true)
    await router.push('/market-radar')
    await flushPromises()
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
    expect(document.body.classList.contains('drawer-open')).toBe(false)
    expect(document.activeElement).toBe(opener.element)
    wrapper.unmount()
  })
})

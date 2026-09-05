import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'

import DataChart from '../src/components/DataChart.vue'
import PaginationControls from '../src/components/PaginationControls.vue'
import SideDrawer from '../src/components/SideDrawer.vue'

describe('F3 交互组件', () => {
  it('关闭抽屉的挂载和卸载不解除另一个已打开抽屉的滚动锁', async () => {
    const opened = mount(SideDrawer, {
      attachTo: document.body,
      props: { open: true, title: '深链详情' },
    })
    await flushPromises()
    const closed = mount(SideDrawer, { props: { open: false, title: '尚未打开' } })
    expect(document.body.classList.contains('drawer-open')).toBe(true)
    closed.unmount()
    expect(document.body.classList.contains('drawer-open')).toBe(true)
    opened.unmount()
    await flushPromises()
    expect(document.body.classList.contains('drawer-open')).toBe(false)
  })

  it.each(['deep-link', 'removed', 'disabled'] as const)(
    '%s 关闭时使用可用回退入口且不滚动',
    async (mode) => {
      const opener = document.createElement('button')
      const fallback = document.createElement('button')
      document.body.append(opener, fallback)
      if (mode !== 'deep-link') opener.focus()
      const focus = vi.spyOn(fallback, 'focus')
      const wrapper = mount(SideDrawer, {
        attachTo: document.body,
        props: { open: true, title: '详情', fallbackFocus: fallback },
      })
      await flushPromises()
      if (mode === 'removed') opener.remove()
      if (mode === 'disabled') opener.disabled = true
      await wrapper.setProps({ open: false })
      await flushPromises()
      expect(document.activeElement).toBe(fallback)
      expect(focus).toHaveBeenCalledWith({ preventScroll: true })
      wrapper.unmount()
    },
  )

  it('Tab 和 Shift+Tab 约束焦点，忽略禁用或负 tabindex 的元素', async () => {
    const wrapper = mount(SideDrawer, {
      attachTo: document.body,
      props: { open: true, title: '键盘详情' },
      slots: {
        default:
          '<button disabled>禁用</button><button tabindex="-1">非入口</button><textarea aria-label="详情文本"></textarea>',
      },
    })
    await flushPromises()
    const first = document.querySelector<HTMLButtonElement>('[aria-label="关闭详情"]')
    const last = document.querySelector<HTMLTextAreaElement>('textarea')
    if (!first || !last) throw new Error('抽屉未显示测试控件')
    document.dispatchEvent(
      new KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, cancelable: true }),
    )
    expect(document.activeElement).toBe(last)
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', cancelable: true }))
    expect(document.activeElement).toBe(first)
    wrapper.unmount()
  })

  it('连续开关后不残留锁或监听，卸载不聚焦已消失的入口', async () => {
    const opener = document.createElement('button')
    document.body.append(opener)
    opener.focus()
    const wrapper = mount(SideDrawer, {
      attachTo: document.body,
      props: { open: false, title: '详情' },
    })
    for (let index = 0; index < 2; index += 1) {
      await wrapper.setProps({ open: true })
      await wrapper.setProps({ open: false })
      await flushPromises()
      expect(document.activeElement).toBe(opener)
    }
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    expect(wrapper.emitted('close')).toBeUndefined()
    await wrapper.setProps({ open: true })
    const focus = vi.spyOn(opener, 'focus')
    opener.remove()
    wrapper.unmount()
    await flushPromises()
    expect(focus).not.toHaveBeenCalled()
    expect(document.body.classList.contains('drawer-open')).toBe(false)
  })
  it('分页只使用后端可证明的范围和 has_more', async () => {
    const wrapper = mount(PaginationControls, {
      props: { offset: 20, limit: 20, itemCount: 20, hasMore: true },
    })
    expect(wrapper.text()).toContain('第 21–40 条 · 第 2 页')

    await wrapper.get('button[aria-label="上一页"]').trigger('click')
    await wrapper.get('button[aria-label="下一页"]').trigger('click')
    expect(wrapper.emitted('change')).toEqual([[0], [40]])
  })

  it('抽屉支持 Escape 关闭并恢复触发点焦点', async () => {
    const opener = document.createElement('button')
    document.body.append(opener)
    opener.focus()
    const wrapper = mount(SideDrawer, {
      attachTo: document.body,
      props: { open: false, title: '订单详情' },
      slots: { default: '<button>抽屉操作</button>' },
    })

    await wrapper.setProps({ open: true })
    expect(document.body.textContent).toContain('订单详情')
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    expect(wrapper.emitted('close')).toEqual([[]])
    await wrapper.setProps({ open: false })
    await flushPromises()
    expect(document.activeElement).toBe(opener)
    wrapper.unmount()
  })

  it('图表提供等价的文字可访问摘要', () => {
    const wrapper = mount(DataChart, {
      props: {
        title: '账户净值',
        kind: 'line',
        valueKind: 'currency',
        points: [
          { label: '2026-01-01', value: 100_000 },
          { label: '2026-01-02', value: 101_000 },
        ],
      },
    })
    expect(wrapper.get('[role="img"]').attributes('aria-label')).toContain('$101,000.00')
  })
})

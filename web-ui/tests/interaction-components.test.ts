import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import DataChart from '../src/components/DataChart.vue'
import PaginationControls from '../src/components/PaginationControls.vue'
import SideDrawer from '../src/components/SideDrawer.vue'

describe('F3 交互组件', () => {
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
    expect(document.activeElement).toBe(opener)
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

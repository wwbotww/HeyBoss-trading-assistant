import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import DataState from '../src/components/DataState.vue'
import DataTable from '../src/components/DataTable.vue'
import SegmentedTabs from '../src/components/SegmentedTabs.vue'

describe('公共状态组件', () => {
  it('宽表滚动区域有名称和键盘入口，保留表格语义', () => {
    const wrapper = mount(DataTable, { props: { caption: '板块价格矩阵' } })
    expect(wrapper.attributes('role')).toBe('region')
    expect(wrapper.attributes('aria-label')).toBe('板块价格矩阵')
    expect(wrapper.attributes('tabindex')).toBe('0')
    expect(wrapper.get('caption').text()).toBe('板块价格矩阵')
    wrapper.unmount()
  })

  it('标签方向键只移动焦点，Enter/Space 手动激活且焦点入口唯一', async () => {
    const wrapper = mount(SegmentedTabs, {
      attachTo: document.body,
      props: {
        modelValue: 'a',
        label: '测试视图',
        tabs: [
          { value: 'a', label: '甲' },
          { value: 'b', label: '乙' },
          { value: 'c', label: '丙' },
        ],
      },
    })
    const buttons = wrapper.findAll<HTMLButtonElement>('button')
    const [first, second, last] = buttons
    if (!first || !second || !last) throw new Error('缺少测试标签')
    expect(wrapper.findAll('[tabindex="0"]')).toHaveLength(1)
    first.element.focus()
    await first.trigger('keydown', { key: 'ArrowLeft' })
    expect(document.activeElement).toBe(last.element)
    await last.trigger('keydown', { key: 'ArrowRight' })
    expect(document.activeElement).toBe(first.element)
    await first.trigger('keydown', { key: 'End' })
    expect(document.activeElement).toBe(last.element)
    await last.trigger('keydown', { key: 'Home' })
    await first.trigger('keydown', { key: 'ArrowRight' })
    expect(document.activeElement).toBe(second.element)
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
    expect(first.attributes('aria-selected')).toBe('true')
    await second.trigger('keydown', { key: 'Enter' })
    await second.trigger('keydown', { key: ' ' })
    expect(wrapper.emitted('update:modelValue')).toEqual([['b'], ['b']])
    expect(wrapper.findAll('[tabindex="0"]')).toHaveLength(1)
    wrapper.unmount()
  })

  it('外部选中态与标签变化同步，不抢走外部焦点', async () => {
    const input = document.createElement('input')
    document.body.append(input)
    const wrapper = mount(SegmentedTabs, {
      attachTo: document.body,
      props: {
        modelValue: 'a',
        label: '测试视图',
        tabs: [
          { value: 'a', label: '甲' },
          { value: 'b', label: '乙' },
        ],
      },
    })
    input.focus()
    await wrapper.setProps({ modelValue: 'b' })
    expect(document.activeElement).toBe(input)
    expect(wrapper.get('[tabindex="0"]').text()).toBe('乙')
    await wrapper.setProps({ tabs: [{ value: 'a', label: '甲', count: 3 }] })
    expect(wrapper.findAll('[tabindex="0"]')).toHaveLength(1)
    expect(document.activeElement).toBe(input)
    wrapper.unmount()
  })
  it.each([
    ['empty', '暂无记录'],
    ['missing', '数据尚未生成'],
    ['invalid', '数据无法读取'],
    ['unconfigured', '尚未配置'],
    ['unobserved', '尚未观测'],
    ['loading', '正在读取'],
    ['error', '暂时无法读取'],
  ] as const)('展示 %s 状态', (state, title) => {
    const wrapper = mount(DataState, { props: { state } })
    expect(wrapper.text()).toContain(title)
    expect(wrapper.attributes('role')).toBe('status')
  })

  it('胶囊标签页只发出界面状态变更', async () => {
    const wrapper = mount(SegmentedTabs, {
      props: {
        modelValue: 'positions',
        label: '账户记录类型',
        tabs: [
          { value: 'positions', label: '当前持仓', count: 1 },
          { value: 'history', label: '资金快照', count: 2 },
        ],
      },
    })

    const historyTab = wrapper.findAll('button')[1]
    if (!historyTab) {
      throw new Error('资金快照标签页不存在')
    }
    await historyTab.trigger('click')
    expect(wrapper.emitted('update:modelValue')).toEqual([['history']])
  })
})

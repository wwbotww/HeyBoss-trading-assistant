import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import DataState from '../src/components/DataState.vue'
import SegmentedTabs from '../src/components/SegmentedTabs.vue'

describe('公共状态组件', () => {
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

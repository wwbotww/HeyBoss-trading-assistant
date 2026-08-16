import { flushPromises } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import SystemPage from '../src/pages/SystemPage.vue'
import { installApiMock } from './fixtures'
import { mountPage } from './helpers'

describe('数据与系统', () => {
  it('分别呈现 Bar 语义、质量报告和可证明来源状态', async () => {
    installApiMock()
    const { wrapper, router } = await mountPage(SystemPage, '/system')
    await flushPromises()

    expect(wrapper.text()).toContain('信号计算')
    expect(wrapper.text()).toContain('模拟成交')
    expect(wrapper.text()).toContain('AAPL.US-1-DAY-LAST-EXTERNAL')

    const qualityTab = wrapper
      .findAll('[role="tab"]')
      .find((tab) => tab.text().includes('数据质量'))
    if (!qualityTab) {
      throw new Error('数据质量标签不存在')
    }
    await qualityTab.trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query.tab).toBe('quality')
    expect(wrapper.text()).toContain('SHORT_HISTORY')

    const sourceTab = wrapper.findAll('[role="tab"]').find((tab) => tab.text().includes('系统来源'))
    if (!sourceTab) {
      throw new Error('系统来源标签不存在')
    }
    await sourceTab.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('尚未观测')
    expect(wrapper.text()).toContain('不等于服务离线')
  })
})

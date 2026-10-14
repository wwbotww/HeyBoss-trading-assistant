import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import type { RadarMetric } from '../src/api/types'
import RadarMetricValue from '../src/components/RadarMetricValue.vue'

const metric: RadarMetric = { value: 0.5593, validity: 'complete', observations: 277, required: 21 }

describe('雷达指标语义', () => {
  it.each([0.5593, 0.0385, -0.1, 0])('风险量 %s 保持中性色和比例单位', (value) => {
    const wrapper = mount(RadarMetricValue, {
      props: { metric: { ...metric, value }, unit: 'percent', colorMode: 'neutral' },
    })
    expect(wrapper.classes()).toContain('radar-metric--neutral')
    expect(wrapper.get('strong').text()).toContain('%')
    expect(wrapper.text()).toContain('可用历史 277 根日线 / 最低需要 21 根')
  })

  it.each([
    [-0.044, '-4.4 个百分点', 'negative'],
    [0.0123, '1.23 个百分点', 'positive'],
    [0, '0 个百分点', 'neutral'],
  ] as const)('收益差 %s 仅换算一次且区分方向', (value, formatted, tone) => {
    const wrapper = mount(RadarMetricValue, {
      props: { metric: { ...metric, value }, unit: 'percentage_points', colorMode: 'directional' },
    })
    expect(wrapper.get('strong').text()).toBe(formatted)
    expect(wrapper.classes()).toContain(`radar-metric--${tone}`)
    expect(wrapper.text()).toContain('两侧历史取较短值')
  })

  it.each(['percent', 'percentage_points'] as const)('%s 的空值不补零', (unit) => {
    const wrapper = mount(RadarMetricValue, {
      props: {
        metric: { ...metric, value: null, validity: 'insufficient_history', observations: 10 },
        unit,
        colorMode: 'directional',
        compact: true,
      },
    })
    expect(wrapper.get('strong').text()).toBe('—')
    expect(wrapper.classes()).toContain('radar-metric--muted')
    expect(wrapper.text()).toContain('可用历史 10 根日线 / 最低需要 21 根')
  })

  it('紧凑模式仍明确可用历史含义，未取得数据保持不可用', async () => {
    const wrapper = mount(RadarMetricValue, {
      props: { metric, unit: 'percent', colorMode: 'neutral', compact: true },
    })
    expect(wrapper.get('strong').text()).toBe('55.93%')
    expect(wrapper.get('span').text()).toBe('可用历史 277 根日线')
    await wrapper.setProps({ metric: { ...metric, value: null, validity: 'unavailable' } })
    expect(wrapper.get('span').text()).toBe('数据不可用')
  })
})

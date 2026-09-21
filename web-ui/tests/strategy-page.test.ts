import { flushPromises } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import StrategyPage from '../src/pages/StrategyPage.vue'
import { factorFixture, installApiMock } from './fixtures'
import { mountPage } from './helpers'

describe('策略与因子', () => {
  it('展示活动配置、完整批次和横截面得分', async () => {
    installApiMock()
    const { wrapper } = await mountPage(StrategyPage, '/strategy')
    await flushPromises()

    expect(wrapper.text()).toContain('PatchTST E3')
    expect(wrapper.text()).toContain('统一风控阈值')
    expect(wrapper.text()).toContain('3 / 3')
    expect(wrapper.text()).toContain('eodhd:isin:US0378331005')
    expect(wrapper.get('[role="img"]').attributes('aria-label')).toContain('因子横截面得分')
  })

  it('因子内容损坏时不渲染伪造排名', async () => {
    installApiMock({
      '/api/factors/latest': {
        ...factorFixture,
        source_state: 'invalid',
        scores: [],
        expected_rows: 0,
      },
    })
    const { wrapper } = await mountPage(StrategyPage, '/strategy')
    await flushPromises()

    expect(wrapper.text()).toContain('完整因子批次不可用')
    expect(wrapper.find('[role="img"]').exists()).toBe(false)
  })
})

describe('缺分展示', () => {
  it('不把不可评分显示为零分或卖出目标', async () => {
    installApiMock({
      '/api/factors/latest': {
        ...factorFixture,
        scores: factorFixture.scores.map((score) =>
          score.symbol === 'NVDA'
            ? { ...score, score: null, eligible: false, selected: false, rank: null }
            : score,
        ),
      },
    })
    const { wrapper } = await mountPage(StrategyPage, '/strategy')
    await flushPromises()
    const row = wrapper.findAll('tbody tr').find((item) => item.text().includes('NVDA'))
    expect(row?.text()).toContain('不可评分')
    expect(row?.text()).toContain('保持持仓 / 不新开仓')
    expect(row?.text()).not.toContain('0.000000')
    expect(wrapper.text()).toContain('权重仅为研究预览')
  })
})

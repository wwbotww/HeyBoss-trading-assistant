import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import type { FundamentalMetric, MarketFundamentals } from '../src/api/types'
import FundamentalMetricValue from '../src/components/FundamentalMetricValue.vue'
import StockFundamentalsPanel from '../src/components/StockFundamentalsPanel.vue'
import { marketFundamentalsFixture } from './fixtures'

describe('基本面指标显示', () => {
  it.each<[FundamentalMetric['name'], number, string]>([
    ['fcf_margin', -0.12, '-12.0%'],
    ['fcf_yield', 0, '0.0%'],
    ['return_on_equity_ttm', 0.2, '20.0%'],
    ['net_debt_to_ebitda', -0.5, '-0.5×'],
    ['forward_pe', 20, '20×'],
    ['enterprise_value_to_ebitda', 11, '11×'],
    ['price_to_book', 3, '3×'],
  ])('%s 保留单位、正负号与零值', (name, value, expected) => {
    const wrapper = mount(FundamentalMetricValue, {
      props: {
        metric: { name, value, reason: null, period_end: '2026-06-30' },
        compact: true,
      },
    })
    expect(wrapper.get('strong').text()).toBe(expected)
    expect(wrapper.text()).toContain('报告期 2026-06-30')
    expect(wrapper.html()).not.toMatch(/positive|negative|is-up|is-down/)
  })

  const reasons: Record<NonNullable<FundamentalMetric['reason']>, string> = {
    missing_field: '来源字段缺失',
    insufficient_history: '不足四个季度',
    non_contiguous_periods: '财报季度不连续',
    period_mismatch: '财报期间未对齐',
    missing_currency: '财报币种未确认',
    currency_mismatch: '财报与上市币种不一致',
    invalid_denominator: '分母非正或不可用',
    non_positive_multiple: '来源倍数非正',
    non_finite_result: '计算结果不是有限数值',
    not_applicable: '不适用于该公司类型',
    unknown_classification: '公司分类未确认',
  }
  it.each(Object.entries(reasons))('%s 的空值原因直接可见', (reason, text) => {
    const wrapper = mount(FundamentalMetricValue, {
      props: {
        metric: {
          name: 'forward_pe',
          value: null,
          reason: reason as FundamentalMetric['reason'],
          period_end: null,
        },
      },
    })
    expect(wrapper.get('strong').text()).toBe('—')
    expect(wrapper.get('.metric-reason').text()).toContain(text)
    expect(wrapper.text()).toContain('报告期未确认')
    expect(wrapper.text()).not.toContain('—×')
  })
})

describe('基本面批次面板', () => {
  it('完整批次区分金融、REIT、未知类型和两条新鲜度', async () => {
    const wrapper = mount(StockFundamentalsPanel, {
      props: { data: marketFundamentalsFixture, loading: false, error: undefined },
    })
    expect(wrapper.findAll('.fundamentals-row')).toHaveLength(4)
    expect(wrapper.text()).toContain('本地快照新鲜')
    expect(wrapper.text()).toContain('来源更新陈旧')
    expect(wrapper.text()).toContain('来源更新未知')
    expect(wrapper.text()).toContain('基本面采集日 · UTC')
    expect(wrapper.text()).toContain('2026-09-05')
    expect(wrapper.text()).toContain('2026-09-01')
    const financial = wrapper.findAll('.fundamentals-row')[1]
    expect(financial?.findAllComponents(FundamentalMetricValue)).toHaveLength(2)
    expect(financial?.text()).toContain('ROE')
    expect(financial?.text()).toContain('P/B')
    expect(financial?.text()).not.toContain('FCF')
    expect(wrapper.text()).toContain('未实现 REIT 专用口径')
    expect(wrapper.text()).toContain('公司分类未确认')
    await wrapper.get('[aria-label="查看 JPM.US 基本面详情"]').trigger('click')
    expect(wrapper.emitted('openStock')).toEqual([['JPM.US']])
  })

  it('陈旧仍展示原值，不把字段可用性改为同步失败', () => {
    const data: MarketFundamentals = {
      ...marketFundamentalsFixture,
      validity: 'complete',
      freshness: {
        snapshot_age_days: 15,
        snapshot_stale_after_days: 14,
        snapshot_state: 'stale',
        source_stale_after_days: 3,
      },
    }
    const wrapper = mount(StockFundamentalsPanel, {
      props: { data, loading: false, error: undefined },
    })
    expect(wrapper.text()).toContain('适用字段完整')
    expect(wrapper.text()).toContain('本地快照陈旧')
    expect(wrapper.text()).toContain('-12.0%')
    expect(wrapper.text()).toContain('不代表同步失败')
  })

  it.each(['missing', 'empty'] as const)('%s 不伪造标的', (source_state) => {
    const wrapper = mount(StockFundamentalsPanel, {
      props: {
        data: { ...marketFundamentalsFixture, source_state, items: [], freshness: null },
        loading: false,
        error: undefined,
      },
    })
    expect(wrapper.text()).toContain('基本面快照尚未发布')
    expect(wrapper.findAll('.fundamentals-row')).toHaveLength(0)
  })

  it('读取状态与重试不需要交易控制', async () => {
    const wrapper = mount(StockFundamentalsPanel, {
      props: { data: undefined, loading: true, error: undefined },
    })
    expect(wrapper.text()).toContain('正在读取基本面快照')
    await wrapper.setProps({ loading: false, error: '基本面暂时无法读取。' })
    expect(wrapper.text()).toContain('基本面快照读取失败')
    await wrapper.get('button').trigger('click')
    expect(wrapper.emitted('retry')).toEqual([[]])
  })
})

import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import type { EventSource, MarketEvents } from '../src/api/types'
import MarketEventsPanel from '../src/components/MarketEventsPanel.vue'
import { marketEventsFixture } from './fixtures'

function mountPanel(data: MarketEvents = structuredClone(marketEventsFixture)) {
  return mount(MarketEventsPanel, {
    attachTo: document.body,
    props: { data, loading: false, refreshing: false, error: undefined, kind: 'all', date: '' },
    global: { stubs: { Teleport: true } },
  })
}

describe('未来事件面板', () => {
  it('显示独立来源范围、十四日与原始时间语义', () => {
    const wrapper = mountPanel()
    expect(wrapper.findAll('.event-source')).toHaveLength(2)
    expect(wrapper.findAll('.event-dates button')).toHaveLength(14)
    expect(wrapper.findAll('.event-day')).toHaveLength(14)
    expect(wrapper.findAll('.event-row')).toHaveLength(4)
    expect(wrapper.text()).toContain('2026-09-05 — 2026-09-18')
    expect(wrapper.text()).toContain('批次陈旧 · 超过 24 小时')
    expect(wrapper.text()).toContain('已发布 watchlist：10 只')
    expect(wrapper.text()).toContain('时区未确认')
    expect(wrapper.text()).toContain('时间未确认')
    expect(wrapper.text()).toContain('时间未知')
    expect(wrapper.text()).not.toContain('09:30')
    wrapper.unmount()
  })

  it('来源与日期只发出筛选动作，筛选后无记录与无来源有区别', async () => {
    const wrapper = mountPanel()
    await wrapper.get('[aria-label="事件来源筛选"] button:last-child').trigger('click')
    expect(wrapper.emitted('update:kind')).toEqual([['earnings']])
    await wrapper.get('[aria-label="筛选 2026-09-07"]').trigger('click')
    expect(wrapper.emitted('update:date')).toEqual([['2026-09-07']])
    await wrapper.setProps({ kind: 'earnings', date: '2026-09-07' })
    expect(wrapper.findAll('.event-day')).toHaveLength(1)
    expect(wrapper.findAll('.event-row')).toHaveLength(0)
    expect(wrapper.text()).toContain('筛选后无记录')
    await wrapper.get('[aria-label="筛选 2026-09-07"]').trigger('click')
    expect(wrapper.emitted('update:date')?.at(-1)).toEqual([''])
    wrapper.unmount()
  })

  it('详情保留零、负值、缺失值及小数精度，不套百分比或 USD', async () => {
    const wrapper = mountPanel()
    const trigger = wrapper.get<HTMLButtonElement>(
      '[aria-label="查看经济事件 Synthetic Price Index"]',
    )
    trigger.element.focus()
    await trigger.trigger('click')
    await flushPromises()
    let drawer = wrapper.get('[role="dialog"]')
    const fields = drawer.findAll('.event-detail-fields dd').map((field) => field.text())
    expect(fields).toContain('0')
    expect(fields).toContain('—')
    expect(fields).toContain('-0.000012345')
    expect(fields).toContain('-0.2')
    expect(fields).not.toContain('-20.0%')
    const close = drawer.get<HTMLButtonElement>('[aria-label="关闭详情"]')
    expect(document.activeElement).toBe(close.element)
    document.dispatchEvent(
      new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true }),
    )
    expect(document.activeElement).toBe(close.element)
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await flushPromises()
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
    expect(document.activeElement).toBe(trigger.element)
    await wrapper.get('[aria-label="查看财报事件 AAPL.US"]').trigger('click')
    await flushPromises()
    drawer = wrapper.get('[role="dialog"]')
    expect(drawer.text()).toContain('币种未确认')
    expect(drawer.text()).not.toContain('USD')
    expect(drawer.text()).toContain('-0.12')
    wrapper.unmount()
  })

  it('刷新按自然键更新详情，撤回或改期关闭而不跳到同一数组位置', async () => {
    const data = structuredClone(marketEventsFixture)
    const wrapper = mountPanel(data)
    await wrapper.get('[aria-label="查看经济事件 Synthetic Price Index"]').trigger('click')
    const updated = structuredClone(data)
    const updatedEvent = updated.days[0]?.economic_events[0]
    if (!updatedEvent) throw new Error('缺少合成经济事件')
    updatedEvent.actual = 2.123456789
    await wrapper.setProps({ data: updated })
    expect(wrapper.get('[role="dialog"]').text()).toContain('2.123456789')
    const moved = structuredClone(updated)
    const movedEvent = moved.days[0]?.economic_events[0]
    if (!movedEvent) throw new Error('缺少合成经济事件')
    movedEvent.event_type = 'Replacement at same index'
    await wrapper.setProps({ data: moved })
    await flushPromises()
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
    expect(document.activeElement).toBe(wrapper.get('.events-reload').element)
    expect(wrapper.text()).toContain('可能已改期、撤回或超出窗口')
    await wrapper.get('[aria-label="查看财报事件 AAPL.US"]').trigger('click')
    const withdrawn = structuredClone(moved)
    const withdrawnDay = withdrawn.days[0]
    if (!withdrawnDay) throw new Error('缺少合成日期')
    withdrawnDay.earnings_events = []
    await wrapper.setProps({ data: withdrawn })
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it.each(['missing', 'empty', 'invalid'] as const)('%s 与合法零条、未覆盖保持区别', (state) => {
    const data = structuredClone(marketEventsFixture)
    data.earnings_source = {
      source: 'eodhd_calendar',
      source_state: state,
      as_of_date: null,
      captured_at_utc: null,
      window_start: null,
      window_end: null,
      freshness: null,
      coverage: null,
      watchlist_count: null,
      window_event_count: null,
    }
    data.days.forEach((day) => {
      day.earnings_events = []
    })
    const wrapper = mountPanel(data)
    expect(wrapper.get('[data-source="earnings"] .event-source-count strong').text()).toBe('—')
    expect(wrapper.get('[data-source="earnings"]').text()).toContain(
      { missing: '未采集', empty: '尚未发布', invalid: '来源损坏' }[state],
    )
    expect(wrapper.get('[aria-label="筛选 2026-09-06"]').text()).toContain('数据有缺口')
    expect(wrapper.findAll('[aria-label="美国经济事件"] .event-row')).toHaveLength(2)
    wrapper.unmount()
  })

  it.each(['covered', 'partial', 'uncovered'] as const)(
    '合法空批次与 %s 日期覆盖不混淆',
    (state) => {
      const data = structuredClone(marketEventsFixture)
      const coverage: EventSource['coverage'] = {
        state,
        covered_start: state === 'uncovered' ? null : '2026-09-05',
        covered_end: state === 'covered' ? '2026-09-18' : state === 'partial' ? '2026-09-07' : null,
        covered_days: state === 'covered' ? 14 : state === 'partial' ? 3 : 0,
      }
      data.economic_source.coverage = coverage
      data.economic_source.window_event_count = state === 'uncovered' ? null : 0
      data.days.forEach((day) => {
        day.economic_events = []
      })
      const wrapper = mountPanel(data)
      expect(wrapper.get('[data-source="economic"] .event-source-count strong').text()).toBe(
        state === 'uncovered' ? '—' : '0',
      )
      expect(wrapper.get('[data-source="economic"]').text()).toContain(
        {
          covered: '该批次在所选窗口未返回事件',
          partial: '仅部分日期已采集',
          uncovered: '请求范围未覆盖此窗口',
        }[state],
      )
      wrapper.unmount()
    },
  )

  it('加载、刷新和失败优先于旧缓存，重读仅发出 retry', async () => {
    const wrapper = mountPanel()
    await wrapper.setProps({ loading: true, refreshing: true })
    expect(wrapper.text()).toContain('正在读取已发布事件')
    expect(wrapper.get('.events-reload').attributes('disabled')).toBeDefined()
    await wrapper.setProps({ loading: false, refreshing: false })
    await wrapper.get('[aria-label="查看财报事件 AAPL.US"]').trigger('click')
    await wrapper.setProps({ error: '来源暂不可读' })
    expect(wrapper.text()).toContain('事件读取失败')
    expect(wrapper.findAll('.event-row')).toHaveLength(0)
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
    await wrapper.get('.data-state--error button').trigger('click')
    expect(wrapper.emitted('retry')).toEqual([[]])
    wrapper.unmount()
  })
})

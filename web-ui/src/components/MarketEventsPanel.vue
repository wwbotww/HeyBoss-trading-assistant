<script setup lang="ts">
import { ArrowUpRight, CalendarDays, RefreshCw } from '@lucide/vue'
import { computed, ref, watch } from 'vue'

import type {
  EconomicEvent,
  EarningsEvent,
  EventSource,
  MarketEventDay,
  MarketEvents,
} from '../api/types'
import { formatDateTime } from '../utils/format'
import DataState from './DataState.vue'
import SegmentedTabs from './SegmentedTabs.vue'
import SideDrawer from './SideDrawer.vue'
import StatusPill from './StatusPill.vue'

const props = defineProps<{
  data: MarketEvents | undefined
  loading: boolean
  refreshing: boolean
  error: string | undefined
  kind: string
  date: string
}>()
const emit = defineEmits<{
  retry: []
  'update:kind': [kind: string]
  'update:date': [date: string]
}>()
const tabs = [
  { value: 'all', label: '全部' },
  { value: 'economic', label: '美国经济' },
  { value: 'earnings', label: '观察股财报' },
]
const sourceLabels = {
  available: '批次可读',
  empty: '尚未发布',
  missing: '未采集',
  invalid: '来源损坏',
  unconfigured: '未配置',
  unobserved: '尚未观测',
}
const sessionLabels = { before_market: '盘前', after_market: '盘后', unknown: '时间未知' }
const sources = computed(() =>
  props.data
    ? [
        { kind: 'economic', label: '美国经济', value: props.data.economic_source },
        { kind: 'earnings', label: '观察股财报', value: props.data.earnings_source },
      ]
    : [],
)
const visibleSources = computed(() =>
  sources.value.filter((source) => props.kind === 'all' || source.kind === props.kind),
)
const days = computed(() =>
  (props.data?.days ?? []).filter((day) => !props.date || day.day === props.date),
)
const selected = ref<{ kind: 'economic' | 'earnings'; key: string } | null>(null)
const notice = ref('')
const reloadButton = ref<HTMLButtonElement | null>(null)

function economicKey(item: EconomicEvent): string {
  return JSON.stringify([
    item.country,
    item.event_date,
    item.source_time,
    item.event_type,
    item.comparison,
    item.period,
  ])
}
function earningsKey(item: EarningsEvent): string {
  return JSON.stringify([item.instrument_id, item.report_date, item.fiscal_period_end])
}
const selectedEvent = computed(() => {
  if (!selected.value || props.error) return null
  if (selected.value.kind === 'economic') {
    const item = props.data?.days
      .flatMap((day) => day.economic_events)
      .find((item) => economicKey(item) === selected.value?.key)
    return item ? { kind: 'economic' as const, item } : null
  }
  const item = props.data?.days
    .flatMap((day) => day.earnings_events)
    .find((item) => earningsKey(item) === selected.value?.key)
  return item ? { kind: 'earnings' as const, item } : null
})
const detailSource = computed(() =>
  selectedEvent.value?.kind === 'economic'
    ? props.data?.economic_source
    : props.data?.earnings_source,
)
const detailTitle = computed(() => {
  const entry = selectedEvent.value
  return entry?.kind === 'economic'
    ? entry.item.event_type
    : entry
      ? `${entry.item.instrument_id} 财报`
      : '事件详情'
})
const detailFields = computed<ReadonlyArray<readonly [string, string | number | null]>>(() => {
  const entry = selectedEvent.value
  if (!entry) return []
  if (entry.kind === 'economic') {
    const item = entry.item
    return [
      ['国家', item.country],
      ['来源报告日期', item.event_date],
      ['来源时钟 · 时区未确认', item.source_time],
      ['比较口径 · comparison', item.comparison],
      ['期间 · period', item.period],
      ['实际值 · actual', item.actual],
      ['预期值 · estimate', item.estimate],
      ['前值 · previous', item.previous],
      ['变化 · change', item.change],
      ['来源变化字段 · change_percentage', item.change_percentage],
    ]
  }
  const item = entry.item
  return [
    ['标的 ID', item.instrument_id],
    ['来源报告日期', item.report_date],
    ['财政期末', item.fiscal_period_end],
    ['发布时段', sessionLabels[item.session]],
    ['EPS 币种', item.currency ?? '币种未确认'],
    ['实际 EPS', item.actual_eps],
    ['预期 EPS', item.estimated_eps],
  ]
})

watch([() => props.data, () => props.error], () => {
  if (selected.value && !selectedEvent.value) {
    selected.value = null
    notice.value = props.error
      ? '事件读取失败，已关闭详情；请重新读取。'
      : '该事件已不在当前响应中，可能已改期、撤回或超出窗口，详情已关闭。'
  }
})

function openEvent(kind: 'economic' | 'earnings', key: string): void {
  notice.value = ''
  selected.value = { kind, key }
}
function covered(source: EventSource, day: string): boolean {
  return (
    source.source_state === 'available' &&
    !!source.coverage?.covered_start &&
    !!source.coverage.covered_end &&
    source.coverage.covered_start <= day &&
    day <= source.coverage.covered_end
  )
}
function daySourceLabel(source: EventSource, day: MarketEventDay): string {
  if (source.source_state !== 'available') return sourceLabels[source.source_state]
  if (!covered(source, day.day)) return '日期未覆盖 / 未采集'
  const count =
    source.source === 'eodhd_calendar' ? day.earnings_events.length : day.economic_events.length
  return `${count ? `${String(count)} 条已读` : '该批次未返回事件'}${source.freshness?.state === 'stale' ? ' · 批次陈旧' : ''}`
}
function dayCount(day: MarketEventDay): string {
  const count = eventCount(day)
  const complete = visibleSources.value.every((source) => covered(source.value, day.day))
  return complete ? `${String(count)} 条` : count ? `${String(count)} 条 · 有缺口` : '数据有缺口'
}
function eventCount(day: MarketEventDay): number {
  return (
    (props.kind !== 'earnings' ? day.economic_events.length : 0) +
    (props.kind !== 'economic' ? day.earnings_events.length : 0)
  )
}
</script>

<template>
  <section class="events-panel" aria-labelledby="events-title" :aria-busy="refreshing">
    <header class="events-header">
      <div>
        <p class="eyebrow"><CalendarDays :size="15" aria-hidden="true" /> Event horizon</p>
        <h3 id="events-title">未来事件</h3>
        <p>含当天的 14 个日历日 · 独立于行情、盈利修正与交易状态</p>
      </div>
      <button
        ref="reloadButton"
        class="events-reload"
        type="button"
        :disabled="refreshing"
        @click="emit('retry')"
      >
        <RefreshCw :size="14" aria-hidden="true" />{{
          refreshing ? '正在读取事件' : '重新读取事件'
        }}
      </button>
    </header>
    <DataState v-if="loading" state="loading" title="正在读取已发布事件" />
    <DataState
      v-else-if="error"
      state="error"
      title="事件读取失败"
      :detail="error"
      retry-label="重试事件读取"
      @retry="emit('retry')"
    />
    <template v-else-if="data">
      <p class="events-read-time">
        窗口 {{ data.window_start }} — {{ data.window_end }} · 读取于
        {{ formatDateTime(data.observed_at_utc) }}。不会自动滚动到次日，请按需重新读取。
      </p>
      <div class="event-sources">
        <article
          v-for="source in sources"
          :key="source.kind"
          class="event-source"
          :data-source="source.kind"
        >
          <header>
            <h4>{{ source.label }}</h4>
            <StatusPill
              :status="source.value.source_state"
              :label="sourceLabels[source.value.source_state]"
            />
          </header>
          <div class="event-source-count">
            <strong>{{ source.value.window_event_count ?? '—' }}</strong
            ><span
              >本窗口已读事件<small>{{ source.value.source }}</small></span
            >
          </div>
          <template v-if="source.value.source_state === 'available'">
            <dl>
              <div>
                <dt>采集完成 · UTC</dt>
                <dd>{{ formatDateTime(source.value.captured_at_utc) }}</dd>
              </div>
              <div>
                <dt>采集日 · UTC</dt>
                <dd>{{ source.value.as_of_date }}</dd>
              </div>
              <div>
                <dt>实际请求范围</dt>
                <dd>{{ source.value.window_start }} — {{ source.value.window_end }}</dd>
              </div>
            </dl>
            <div class="event-source-badges">
              <StatusPill
                :status="source.value.freshness?.state || 'unknown'"
                :label="
                  source.value.freshness?.state === 'stale'
                    ? '批次陈旧 · 超过 24 小时'
                    : '采集距读取不超过 24 小时'
                "
              />
              <StatusPill
                :status="source.value.coverage?.state === 'covered' ? 'neutral' : 'partial'"
                :label="`日期覆盖 ${source.value.coverage?.covered_days ?? 0} / 14 日`"
              />
            </div>
            <p v-if="source.value.coverage?.state !== 'covered'" class="event-gap">
              {{
                source.value.coverage?.state === 'partial'
                  ? '仅部分日期已采集，事件数不代表整个窗口。'
                  : '请求范围未覆盖此窗口，不能解释为零事件。'
              }}
            </p>
            <p v-else-if="source.value.window_event_count === 0" class="event-source-note">
              该批次在所选窗口未返回事件。
            </p>
            <p v-if="source.kind === 'earnings'" class="event-source-note">
              该次已发布 watchlist：{{ source.value.watchlist_count }}
              只；未保存完整请求名单，不代表当前观察池逐股检查结果。
            </p>
          </template>
          <p v-else class="event-gap">
            {{
              source.value.source_state === 'invalid'
                ? '来源内容未通过校验。此来源事件不展示，其他来源不受影响。'
                : '尚无可读取的已发布批次；页面不会自动采集。'
            }}
          </p>
        </article>
      </div>
      <p class="events-boundary">
        覆盖仅表示请求日期范围，不保证供应商事件完整。来源日期与时钟未经时区确认；不提供重要性、利好利空判断或交易动作。
      </p>
      <div class="event-filters">
        <SegmentedTabs
          :model-value="kind"
          :tabs="tabs"
          label="事件来源筛选"
          @update:model-value="emit('update:kind', $event)"
        />
      </div>
      <div class="event-date-header">
        <span>按来源报告日期浏览</span
        ><button type="button" :aria-pressed="!date" @click="emit('update:date', '')">
          全部日期
        </button>
      </div>
      <div class="event-dates" role="group" aria-label="事件日期筛选">
        <button
          v-for="day in data.days"
          :key="day.day"
          type="button"
          :aria-label="`筛选 ${day.day}`"
          :aria-pressed="date === day.day"
          @click="emit('update:date', date === day.day ? '' : day.day)"
        >
          <strong>{{ day.day.slice(5) }}</strong
          ><small>{{ dayCount(day) }}</small>
        </button>
      </div>
      <p v-if="notice" role="status" class="event-gap">{{ notice }}</p>
      <div class="event-days" aria-label="按日事件列表">
        <article v-for="day in days" :key="day.day" class="event-day">
          <header>
            <h4>{{ day.day }}</h4>
            <span v-if="day.day === data.window_start">窗口首日</span>
          </header>
          <div class="event-day-content">
            <div class="day-source-states">
              <p v-for="source in visibleSources" :key="source.kind">
                {{ source.label }} · {{ daySourceLabel(source.value, day) }}
              </p>
            </div>
            <ul v-if="kind !== 'earnings' && day.economic_events.length" aria-label="美国经济事件">
              <li v-for="item in day.economic_events" :key="economicKey(item)">
                <button
                  class="event-row"
                  type="button"
                  :aria-label="`查看经济事件 ${item.event_type}`"
                  @click="openEvent('economic', economicKey(item))"
                >
                  <span class="event-identity"
                    ><small
                      >美国经济 · {{ item.comparison || '比较口径未确认' }} ·
                      {{ item.period || '期间未确认' }}</small
                    ><strong>{{ item.event_type }}</strong></span
                  >
                  <span class="event-time"
                    >{{ item.source_time || '时间未确认'
                    }}<small v-if="item.source_time">来源时钟 · 时区未确认</small></span
                  ><ArrowUpRight :size="16" aria-hidden="true" />
                </button>
              </li>
            </ul>
            <ul
              v-if="kind !== 'economic' && day.earnings_events.length"
              aria-label="观察股财报事件"
            >
              <li v-for="item in day.earnings_events" :key="earningsKey(item)">
                <button
                  class="event-row"
                  type="button"
                  :aria-label="`查看财报事件 ${item.instrument_id}`"
                  @click="openEvent('earnings', earningsKey(item))"
                >
                  <span class="event-identity"
                    ><small>观察股财报 · 财政期 {{ item.fiscal_period_end }}</small
                    ><strong>{{ item.instrument_id }}</strong></span
                  >
                  <span class="event-time">{{ sessionLabels[item.session] }}</span
                  ><ArrowUpRight :size="16" aria-hidden="true" />
                </button>
              </li>
            </ul>
            <p
              v-if="
                eventCount(day) === 0 && day.economic_events.length + day.earnings_events.length > 0
              "
              class="event-source-note"
            >
              筛选后无记录；其他来源有已读事件。
            </p>
          </div>
        </article>
      </div>
    </template>
    <SideDrawer
      :open="!!selectedEvent"
      :fallback-focus="reloadButton"
      :title="selectedEvent?.kind === 'economic' ? '美国经济事件' : '观察股财报事件'"
      description="已发布事件字段 · 不提供精确 UTC 发布时间或交易建议"
      @close="selected = null"
    >
      <template v-if="selectedEvent">
        <h4 class="event-detail-name">{{ detailTitle }}</h4>
        <p class="events-boundary">
          {{
            selectedEvent.kind === 'economic'
              ? '数值原样展示，单位未确认；change_percentage 不乘 100，也不标成已确认百分比。'
              : '财报时段保留盘前 / 盘后 / 未知，不映射成精确开收盘时间。'
          }}
          缺失值用 — 表示来源未提供，不补零、不推导 surprise。
        </p>
        <dl class="event-detail-fields">
          <div v-for="[label, value] in detailFields" :key="label">
            <dt>{{ label }}</dt>
            <dd>{{ value === null ? '—' : String(value) }}</dd>
          </div>
        </dl>
        <h4>来源与批次</h4>
        <dl class="event-detail-fields">
          <div>
            <dt>数据来源</dt>
            <dd>{{ detailSource?.source }}</dd>
          </div>
          <div>
            <dt>采集完成 · UTC</dt>
            <dd>{{ formatDateTime(detailSource?.captured_at_utc) }}</dd>
          </div>
          <div>
            <dt>实际请求范围</dt>
            <dd>{{ detailSource?.window_start }} — {{ detailSource?.window_end }}</dd>
          </div>
          <div>
            <dt>读取时新鲜度</dt>
            <dd>
              {{
                detailSource?.freshness?.state === 'stale'
                  ? '批次陈旧 · 超过 24 小时'
                  : '不超过 24 小时'
              }}
            </dd>
          </div>
        </dl>
      </template>
    </SideDrawer>
  </section>
</template>

<style scoped>
.events-panel {
  padding: 28px;
  border: 1px solid var(--color-line);
  border-radius: var(--radius-lg);
  background: var(--color-surface);
  min-width: 0;
}
.events-header,
.event-source > header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 12px;
}
.events-header .eyebrow {
  display: flex;
  gap: 8px;
  align-items: center;
}
.events-header .eyebrow svg {
  color: var(--color-info);
}
h3 {
  margin: 6px 0 0;
  font-size: 1.3rem;
  letter-spacing: -0.035em;
}
h4 {
  margin: 0;
  font-size: 0.82rem;
}
.events-header p:last-child,
.events-read-time,
.events-boundary,
.event-source-note,
.event-gap {
  font-size: 0.72rem;
  color: var(--color-text-soft);
  line-height: 1.65;
  overflow-wrap: anywhere;
}
.events-read-time {
  margin: 18px 0;
}
.events-reload {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  border: 1px solid var(--color-line);
  border-radius: 999px;
  background: var(--color-surface);
  font-size: 0.72rem;
  cursor: pointer;
}
.events-reload:disabled {
  opacity: 0.6;
  cursor: wait;
}
.event-sources {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
}
.event-source {
  padding: 20px;
  border: 1px solid var(--color-line);
  border-radius: var(--radius-md);
  min-width: 0;
}
.event-source-count {
  display: flex;
  align-items: center;
  gap: 14px;
  margin: 18px 0;
}
.event-source-count > strong {
  font-size: 2.1rem;
  line-height: 1;
  font-weight: 600;
  letter-spacing: -0.045em;
  font-variant-numeric: tabular-nums;
}
.event-source-count span {
  font-size: 0.7rem;
  color: var(--color-text-soft);
}
.event-source-count small {
  display: block;
  margin-top: 5px;
  font-size: 0.64rem;
  overflow-wrap: anywhere;
}
.event-source dl {
  display: grid;
  gap: 8px;
  margin: 0;
}
.event-source dl > div {
  display: flex;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 4px 12px;
  font-size: 0.68rem;
}
dt {
  color: var(--color-text-faint);
}
dd {
  margin: 0;
  overflow-wrap: anywhere;
}
.event-source-badges {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 16px;
}
.event-gap {
  color: var(--color-warning);
}
.events-boundary {
  padding: 12px 14px;
  background: var(--color-surface-soft);
  border-radius: var(--radius-sm);
  margin: 16px 0 22px;
}
.event-filters {
  margin-bottom: 22px;
}
.event-filters :deep(.segmented-tabs) {
  max-width: 100%;
  flex-wrap: wrap;
  border-radius: 22px;
}
.event-filters :deep(button) {
  padding: 0 12px;
  font-size: 0.74rem;
}
.event-date-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
  font-size: 0.72rem;
  color: var(--color-text-soft);
}
.event-date-header button {
  background: transparent;
  color: inherit;
  padding: 7px 12px;
  border-radius: 999px;
  cursor: pointer;
}
.event-date-header button[aria-pressed='true'] {
  background: var(--color-text);
  color: var(--color-surface);
}
.event-dates {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(76px, 1fr));
  gap: 7px;
}
.event-dates button {
  display: grid;
  gap: 7px;
  padding: 12px 4px;
  text-align: center;
  background: var(--color-surface-soft);
  border: 1px solid transparent;
  border-radius: 14px;
  cursor: pointer;
}
.event-dates strong {
  font-size: 0.8rem;
  font-variant-numeric: tabular-nums;
}
.event-dates small {
  font-size: 0.6rem;
  color: var(--color-text-soft);
}
.event-dates button[aria-pressed='true'] {
  border-color: var(--color-text);
  background: var(--color-surface);
}
.event-dates button:hover,
.event-row:hover {
  background: var(--color-surface-soft);
  border-color: var(--color-line-strong);
}
.event-days {
  margin-top: 22px;
}
.event-day {
  display: grid;
  grid-template-columns: 110px minmax(0, 1fr);
  gap: 20px;
  padding: 18px 0;
  border-top: 1px solid var(--color-line);
}
.event-day > header {
  padding-top: 2px;
}
.event-day > header span {
  display: block;
  margin-top: 6px;
  font-size: 0.64rem;
  color: var(--color-text-faint);
}
.day-source-states {
  display: flex;
  flex-wrap: wrap;
  gap: 5px 18px;
}
.day-source-states p {
  margin: 0;
  color: var(--color-text-soft);
  font-size: 0.65rem;
  line-height: 1.6;
}
.event-day ul {
  list-style: none;
  padding: 0;
  margin: 10px 0 0;
}
.event-row {
  width: 100%;
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto 16px;
  align-items: center;
  gap: 18px;
  padding: 14px 12px;
  text-align: left;
  background: var(--color-surface);
  border: 1px solid var(--color-line);
  border-radius: 12px;
  cursor: pointer;
  margin-top: 7px;
}
.event-identity {
  display: grid;
  gap: 6px;
  min-width: 0;
}
.event-identity strong {
  font-size: 0.81rem;
  font-weight: 600;
  overflow-wrap: anywhere;
}
.event-row small {
  display: block;
  font-size: 0.64rem;
  color: var(--color-text-soft);
  line-height: 1.5;
  overflow-wrap: anywhere;
}
.event-time {
  font-size: 0.7rem;
  text-align: right;
}
.event-time small {
  margin-top: 5px;
}
.event-detail-fields {
  display: grid;
  gap: 0;
  margin: 16px 0 28px;
}
.event-detail-fields > div {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 18px;
  padding: 14px 0;
  border-bottom: 1px solid var(--color-line);
  font-size: 0.77rem;
  line-height: 1.6;
}
.event-detail-fields dt {
  overflow-wrap: anywhere;
}
.event-detail-fields dd {
  font-variant-numeric: tabular-nums;
}
.event-detail-name {
  overflow-wrap: anywhere;
  line-height: 1.6;
}
@media (max-width: 900px) {
  .event-sources {
    grid-template-columns: minmax(0, 1fr);
  }
}
@media (max-width: 600px) {
  .events-panel {
    padding: 18px 16px;
  }
  .event-source {
    padding: 16px;
  }
  .event-day {
    grid-template-columns: minmax(0, 1fr);
    gap: 10px;
  }
  .event-day > header {
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .event-day > header span {
    margin: 0;
  }
  .event-row {
    grid-template-columns: minmax(0, 1fr) 16px;
    gap: 10px;
  }
  .event-time {
    grid-row: 2;
    text-align: left;
  }
  .event-row > svg {
    grid-column: 2;
    grid-row: 1 / 3;
  }
}
</style>

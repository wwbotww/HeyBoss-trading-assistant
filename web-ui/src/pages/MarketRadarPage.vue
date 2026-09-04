<script setup lang="ts">
import {
  ArrowDownUp,
  ChevronRight,
  CircleOff,
  Radar as RadarIcon,
  Search,
  ShieldCheck,
} from '@lucide/vue'
import { useQuery } from '@tanstack/vue-query'
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter, type LocationQueryRaw } from 'vue-router'

import {
  marketRadarBreadthQuery,
  marketRadarMacroQuery,
  marketRadarSectorQuery,
  marketRadarSectorsQuery,
  marketRadarStockQuery,
  marketRadarStocksQuery,
  marketRadarSummaryQuery,
} from '../api/queries'
import type { BreadthMetric, RadarMetric, StockRadarQuery } from '../api/types'
import DataState from '../components/DataState.vue'
import DataTable from '../components/DataTable.vue'
import MacroRegimeChart from '../components/MacroRegimeChart.vue'
import PaginationControls from '../components/PaginationControls.vue'
import RadarMetricValue from '../components/RadarMetricValue.vue'
import SegmentedTabs from '../components/SegmentedTabs.vue'
import SideDrawer from '../components/SideDrawer.vue'
import StatusPill from '../components/StatusPill.vue'
import { formatDateTime, formatNumber, formatPercent, formatSourceState } from '../utils/format'
import { PAGE_SIZE, pageOffset, readPage, readQueryText, withPage } from '../utils/pagination'

type RadarView = 'overview' | 'sectors' | 'stocks'
type StockSort = NonNullable<StockRadarQuery['sort']>
type SortDirection = NonNullable<StockRadarQuery['direction']>

const sectorLabels: Readonly<Record<string, string>> = {
  communication_services: '通信服务',
  consumer_discretionary: '可选消费',
  consumer_staples: '必需消费',
  energy: '能源',
  financials: '金融',
  health_care: '医疗保健',
  industrials: '工业',
  information_technology: '信息技术',
  materials: '原材料',
  real_estate: '房地产',
  utilities: '公用事业',
}
const stockSortValues: readonly StockSort[] = [
  'instrument',
  'momentum',
  'relative_momentum',
  'volatility',
  'drawdown',
]

const route = useRoute()
const router = useRouter()
const activeView = computed<RadarView>({
  get: () => {
    const view = route.query.view
    return view === 'sectors' || view === 'stocks' ? view : 'overview'
  },
  set: (value) => {
    const query: LocationQueryRaw = { view: value }
    void router.replace({ query })
  },
})
const page = computed(() => readPage(route.query.page))
const sectorFilter = computed(() => readQueryText(route.query.sector))
const stockSearch = computed(() => readQueryText(route.query.query))
const selectedSectorId = computed(() =>
  activeView.value === 'sectors' ? readQueryText(route.query.sector) : '',
)
const selectedInstrumentId = computed(() =>
  activeView.value === 'stocks' ? readQueryText(route.query.instrument) : '',
)
const stockSort = computed<StockSort>(() => {
  const value = readQueryText(route.query.sort)
  return stockSortValues.includes(value as StockSort) ? (value as StockSort) : 'instrument'
})
const sortDirection = computed<SortDirection>(() =>
  route.query.direction === 'desc' ? 'desc' : 'asc',
)
const searchDraft = ref(stockSearch.value)

watch(stockSearch, (value) => {
  searchDraft.value = value
})

const stockParams = computed<StockRadarQuery>(() => {
  const query: StockRadarQuery = {
    offset: pageOffset(page.value),
    limit: PAGE_SIZE,
    sort: stockSort.value,
    direction: sortDirection.value,
  }
  if (sectorFilter.value) {
    query.sector = sectorFilter.value
  }
  if (stockSearch.value) {
    query.query = stockSearch.value
  }
  return query
})

const summary = useQuery(marketRadarSummaryQuery())
const breadth = useQuery(marketRadarBreadthQuery())
const macro = useQuery(marketRadarMacroQuery())
const sectors = useQuery(marketRadarSectorsQuery())
const stocks = useQuery(computed(() => marketRadarStocksQuery(stockParams.value)))
const sectorDetail = useQuery(
  computed(() => marketRadarSectorQuery(selectedSectorId.value, selectedSectorId.value.length > 0)),
)
const stockDetail = useQuery(
  computed(() =>
    marketRadarStockQuery(selectedInstrumentId.value, selectedInstrumentId.value.length > 0),
  ),
)

const tabs = computed(() => [
  { value: 'overview', label: '总览', count: summary.data.value?.modules.length ?? 0 },
  { value: 'sectors', label: '板块', count: sectors.data.value?.items.length ?? 0 },
  { value: 'stocks', label: '个股', count: stocks.data.value?.items.length ?? 0 },
])
const sectorOptions = computed(() =>
  [...(sectors.data.value?.items ?? [])].sort((left, right) =>
    sectorName(left.sector_id).localeCompare(sectorName(right.sector_id), 'zh-CN'),
  ),
)
const breadthMetrics = computed<
  ReadonlyArray<{ id: string; label: string; description: string; metric: BreadthMetric | null }>
>(() => [
  {
    id: 'b50',
    label: 'B50',
    description: '收盘高于 50 日均线',
    metric: breadth.data.value?.b50 ?? null,
  },
  {
    id: 'b200',
    label: 'B200',
    description: '收盘高于 200 日均线',
    metric: breadth.data.value?.b200 ?? null,
  },
  {
    id: 'ad10',
    label: 'AD10',
    description: '10 日涨跌扩散 EMA',
    metric: breadth.data.value?.ad10 ?? null,
  },
  {
    id: 'nhnl',
    label: 'NHNL',
    description: '52 周新高减新低',
    metric: breadth.data.value?.nhnl ?? null,
  },
])

function sectorName(sectorId: string): string {
  return sectorLabels[sectorId] ?? sectorId.replaceAll('_', ' ')
}

function moduleStateLabel(state: string): string {
  const labels: Readonly<Record<string, string>> = {
    complete: '完整',
    partial: '部分可用',
    stale: '已陈旧',
    insufficient_history: '历史不足',
    insufficient_coverage: '覆盖不足',
    unavailable: '不可用',
  }
  return labels[state] ?? state
}

function breadthSourceLabel(source: string | null): string {
  if (!source) {
    return '—'
  }
  return source === 'state_street_spy_holdings' ? 'State Street SPY 官方持仓' : source
}

function metricCellTone(metric: RadarMetric): string {
  if (metric.value === null) {
    return 'heat-unavailable'
  }
  if (metric.value >= 0.05) {
    return 'heat-positive-strong'
  }
  if (metric.value > 0) {
    return 'heat-positive'
  }
  if (metric.value <= -0.05) {
    return 'heat-negative-strong'
  }
  if (metric.value < 0) {
    return 'heat-negative'
  }
  return 'heat-neutral'
}

function updateStockQuery(updates: LocationQueryRaw): void {
  const query: LocationQueryRaw = { ...route.query, ...updates, view: 'stocks' }
  delete query.page
  delete query.instrument
  if (query.sector === '') {
    delete query.sector
  }
  if (query.query === '') {
    delete query.query
  }
  if (query.sort === '') {
    delete query.sort
  }
  if (query.direction === '') {
    delete query.direction
  }
  void router.replace({ query })
}

function changeSectorFilter(event: Event): void {
  const target = event.target
  if (target instanceof HTMLSelectElement) {
    updateStockQuery({ sector: target.value })
  }
}

function changeSort(event: Event): void {
  const target = event.target
  if (target instanceof HTMLSelectElement) {
    updateStockQuery({ sort: target.value })
  }
}

function changeDirection(event: Event): void {
  const target = event.target
  if (target instanceof HTMLSelectElement) {
    updateStockQuery({ direction: target.value })
  }
}

function applySearch(): void {
  updateStockQuery({ query: searchDraft.value.trim() })
}

function clearStockFilters(): void {
  searchDraft.value = ''
  void router.replace({ query: { view: 'stocks' } })
}

function changeOffset(offset: number): void {
  void router.replace({ query: withPage(route.query, Math.floor(offset / PAGE_SIZE) + 1) })
}

function openSector(sectorId: string): void {
  void router.replace({ query: { view: 'sectors', sector: sectorId } })
}

function closeSector(): void {
  void router.replace({ query: { view: 'sectors' } })
}

function openStock(instrumentId: string): void {
  void router.replace({ query: { ...route.query, view: 'stocks', instrument: instrumentId } })
}

function closeStock(): void {
  const query: LocationQueryRaw = { ...route.query }
  delete query.instrument
  void router.replace({ query })
}

async function retrySummary(): Promise<void> {
  await summary.refetch()
}

async function retryBreadth(): Promise<void> {
  await breadth.refetch()
}

async function retryMacro(): Promise<void> {
  await macro.refetch()
}

async function retrySectors(): Promise<void> {
  await sectors.refetch()
}

async function retryStocks(): Promise<void> {
  await stocks.refetch()
}
</script>

<template>
  <div class="page-stack radar-page">
    <div class="page-intro">
      <div>
        <h1>市场雷达</h1>
        <p>读取最近一次原子发布的价格、宽度与宏观快照，观察市场结构；不生成交易信号。</p>
      </div>
      <StatusPill
        v-if="summary.data.value"
        :status="summary.data.value.source_state"
        :label="`价格快照 · ${formatSourceState(summary.data.value.source_state)}`"
      />
    </div>

    <section class="surface radar-workspace">
      <header class="radar-toolbar">
        <div>
          <p class="eyebrow">Read-only market intelligence</p>
          <h2>
            {{
              activeView === 'overview'
                ? '市场状态'
                : activeView === 'sectors'
                  ? '板块强弱'
                  : '个股雷达'
            }}
          </h2>
        </div>
        <SegmentedTabs v-model="activeView" :tabs="tabs" label="市场雷达视图" />
      </header>

      <div v-if="summary.data.value" class="snapshot-strip">
        <div>
          <span>数据日期</span>
          <strong class="tabular">{{ summary.data.value.as_of_date || '—' }}</strong>
        </div>
        <div>
          <span>计算时间</span>
          <strong>{{ formatDateTime(summary.data.value.calculated_at_utc) }}</strong>
        </div>
        <div v-if="summary.data.value.coverage">
          <span>当日覆盖</span>
          <strong class="tabular">
            {{ summary.data.value.coverage.observed }} / {{ summary.data.value.coverage.eligible }}
          </strong>
        </div>
        <p><ShieldCheck :size="15" aria-hidden="true" />只读快照不会触发同步、计算或交易。</p>
      </div>

      <template v-if="activeView === 'overview'">
        <DataState v-if="summary.isPending.value" state="loading" />
        <DataState
          v-else-if="summary.isError.value"
          state="error"
          :detail="summary.error.value?.message"
          retry-label="重新读取市场摘要"
          @retry="retrySummary"
        />
        <DataState
          v-else-if="summary.data.value?.source_state !== 'available'"
          :state="summary.data.value?.source_state || 'empty'"
          title="完整价格快照不可用"
          detail="先运行市场雷达价格同步；页面不会读取 Catalog 或临时计算指标。"
        />
        <template v-else-if="summary.data.value">
          <section class="module-grid" aria-label="市场雷达六项能力状态">
            <article
              v-for="module in summary.data.value.modules"
              :key="module.module_id"
              class="module-card"
            >
              <div>
                <span>{{ module.label }}</span>
                <StatusPill :status="module.state" :label="moduleStateLabel(module.state)" />
              </div>
              <p>{{ module.detail }}</p>
            </article>
          </section>

          <section v-if="summary.data.value.market" class="price-summary">
            <article class="price-hero">
              <div class="radar-orbit" aria-hidden="true"><RadarIcon :size="28" /></div>
              <div>
                <p class="eyebrow">Price snapshot</p>
                <h3>SPY 与等权确认</h3>
                <p>只展示当前完整横截面。数据库尚未保存可供图表使用的连续时间序列。</p>
              </div>
              <strong class="coverage-value">
                {{ formatPercent(summary.data.value.coverage?.ratio) }}
                <small>监测池覆盖</small>
              </strong>
            </article>
            <div class="market-metric-grid">
              <article>
                <span>SPY · 20 日收益</span>
                <RadarMetricValue :metric="summary.data.value.market.spy_return_20" />
              </article>
              <article>
                <span>SPY · 距 MA200</span>
                <RadarMetricValue :metric="summary.data.value.market.spy_distance_ma_200" />
              </article>
              <article>
                <span>RSP / SPY · 20 日</span>
                <RadarMetricValue :metric="summary.data.value.market.rsp_spy_return_20" />
              </article>
            </div>
          </section>
        </template>

        <section class="macro-section" aria-labelledby="macro-title">
          <header class="macro-header">
            <div>
              <p class="eyebrow">Macro regime</p>
              <h3 id="macro-title">实际利率 × 风险偏好</h3>
              <p>横轴使用 DFII10 的 20 观测变化 Robust Z，纵轴组合信用代理与波动率期限结构。</p>
            </div>
            <StatusPill
              v-if="macro.data.value"
              :status="macro.data.value.validity"
              :label="moduleStateLabel(macro.data.value.validity)"
            />
          </header>

          <DataState v-if="macro.isPending.value" state="loading" />
          <DataState
            v-else-if="macro.isError.value"
            state="error"
            :detail="macro.error.value?.message"
            retry-label="重新读取宏观象限"
            @retry="retryMacro"
          />
          <DataState
            v-else-if="macro.data.value?.source_state !== 'available'"
            :state="macro.data.value?.source_state || 'empty'"
            title="宏观象限快照不可用"
            detail="先运行宏观离线同步；页面不会连接 FRED、扫描 Catalog 或临时计算象限。"
          />
          <template v-else-if="macro.data.value">
            <div class="macro-meta">
              <div>
                <span>状态日期</span>
                <strong class="tabular">{{ macro.data.value.as_of_date || '—' }}</strong>
              </div>
              <div>
                <span>实际利率来源</span>
                <strong>FRED · {{ macro.data.value.real_rate?.series_id || '—' }}</strong>
              </div>
              <div>
                <span>修订口径</span>
                <strong>{{
                  macro.data.value.real_rate_vintage === 'current' ? '当前修订' : '—'
                }}</strong>
              </div>
              <div>
                <span>双轴新鲜度</span>
                <strong v-if="macro.data.value.freshness" class="tabular">
                  利率 {{ macro.data.value.freshness.real_rate_age_days }} 天 · 风险
                  {{ macro.data.value.freshness.risk_appetite_age_days }} 天
                </strong>
                <strong v-else>—</strong>
              </div>
            </div>

            <div class="macro-layout">
              <aside class="macro-reading">
                <div class="macro-current">
                  <span>当前宏观状态</span>
                  <strong>{{
                    macro.data.value.current?.regime_label ||
                    moduleStateLabel(macro.data.value.validity)
                  }}</strong>
                  <small v-if="macro.data.value.current">
                    已连续 {{ macro.data.value.duration_observations }} 个有效观测
                  </small>
                  <small v-else>当前双轴不足以完成后端分类</small>
                </div>
                <div class="macro-axis-grid">
                  <article>
                    <span>实际利率</span>
                    <strong class="tabular">
                      {{ formatNumber(macro.data.value.real_rate?.level_percent, 2) }}%
                    </strong>
                    <small class="tabular">
                      20 期变化
                      {{ formatNumber(macro.data.value.real_rate?.change_20_percentage_points, 3) }}
                      pp
                    </small>
                  </article>
                  <article>
                    <span>利率压力 Z</span>
                    <strong class="tabular">{{
                      formatNumber(macro.data.value.real_rate?.pressure_z, 2)
                    }}</strong>
                    <small>
                      {{ macro.data.value.real_rate?.observations || 0 }} /
                      {{ macro.data.value.real_rate?.required || 0 }} 个变化观测
                    </small>
                  </article>
                  <article>
                    <span>风险偏好</span>
                    <strong class="tabular">{{
                      formatNumber(macro.data.value.risk_appetite?.score, 2)
                    }}</strong>
                    <small class="tabular">
                      Credit Z {{ formatNumber(macro.data.value.risk_appetite?.credit_z, 2) }} · Vol
                      Z
                      {{ formatNumber(macro.data.value.risk_appetite?.volatility_z, 2) }}
                    </small>
                  </article>
                </div>
                <p class="macro-provenance">
                  信用轴为 HYG/LQD ETF 代理；价格来自 EODHD → NT Catalog。FRED 仅保存当前修订，
                  不具备历史 vintage / PIT 回放语义。
                </p>
              </aside>
              <div class="macro-visual">
                <MacroRegimeChart v-if="macro.data.value.current" :macro="macro.data.value" />
                <DataState
                  v-else
                  state="empty"
                  title="暂不能绘制宏观象限"
                  detail="只有双轴历史和日期对齐均满足契约时，页面才展示当前点与轨迹。"
                />
              </div>
            </div>
            <p v-if="macro.data.value.validity === 'stale'" class="macro-notice">
              至少一条宏观轴已超过 3 个日历日未更新；图中保留最后可追溯状态，但不视为当前状态。
            </p>
          </template>
        </section>

        <section class="breadth-section" aria-labelledby="breadth-title">
          <header class="breadth-header">
            <div>
              <p class="eyebrow">Current breadth snapshot</p>
              <h3 id="breadth-title">当前市场宽度</h3>
              <p>使用最新 SPY 当前持仓代理观察横截面，只描述当前结构，不代表历史 PIT 指数宽度。</p>
            </div>
            <StatusPill
              v-if="breadth.data.value"
              :status="breadth.data.value.validity"
              :label="moduleStateLabel(breadth.data.value.validity)"
            />
          </header>

          <DataState v-if="breadth.isPending.value" state="loading" />
          <DataState
            v-else-if="breadth.isError.value"
            state="error"
            :detail="breadth.error.value?.message"
            retry-label="重新读取当前宽度"
            @retry="retryBreadth"
          />
          <DataState
            v-else-if="breadth.data.value?.source_state !== 'available'"
            :state="breadth.data.value?.source_state || 'empty'"
            title="当前宽度快照不可用"
            detail="先运行当前宽度同步；页面不会下载持仓、扫描 Catalog 或临时计算指标。"
          />
          <template v-else-if="breadth.data.value">
            <div class="breadth-meta">
              <div>
                <span>成员口径</span>
                <strong>SPY 当前持仓代理</strong>
              </div>
              <div>
                <span>代理来源</span>
                <strong>{{ breadthSourceLabel(breadth.data.value.membership_source) }}</strong>
              </div>
              <div>
                <span>持仓日期</span>
                <strong class="tabular">{{ breadth.data.value.membership_date || '—' }}</strong>
              </div>
              <div>
                <span>价格日期</span>
                <strong class="tabular">{{ breadth.data.value.as_of_date || '—' }}</strong>
              </div>
              <div>
                <span>成员新鲜度</span>
                <strong v-if="breadth.data.value.freshness" class="tabular">
                  {{ breadth.data.value.freshness.membership_age_days }} 天 / 阈值
                  {{ breadth.data.value.freshness.stale_after_days }} 天
                </strong>
                <strong v-else>—</strong>
              </div>
              <div>
                <span>计算时间</span>
                <strong>{{ formatDateTime(breadth.data.value.calculated_at_utc) }}</strong>
              </div>
            </div>

            <div class="breadth-grid">
              <article
                v-for="item in breadthMetrics"
                :key="item.id"
                class="breadth-card"
                :class="item.metric ? `breadth-card--${item.metric.validity}` : ''"
              >
                <template v-if="item.metric">
                  <div class="breadth-card-heading">
                    <div>
                      <span>{{ item.label }}</span>
                      <small>{{ item.description }}</small>
                    </div>
                    <StatusPill
                      :status="item.metric.validity"
                      :label="moduleStateLabel(item.metric.validity)"
                      :dot="false"
                    />
                  </div>
                  <strong class="breadth-value tabular">{{
                    formatPercent(item.metric.value)
                  }}</strong>
                  <div class="breadth-coverage">
                    <div>
                      <span>有效成员</span>
                      <strong class="tabular">
                        {{ item.metric.coverage.observed }} / {{ item.metric.coverage.eligible }}
                      </strong>
                    </div>
                    <div>
                      <span>覆盖率</span>
                      <strong class="tabular">{{
                        formatPercent(item.metric.coverage.ratio)
                      }}</strong>
                    </div>
                    <div>
                      <span>历史要求</span>
                      <strong class="tabular">{{ item.metric.history_required }} 根日线</strong>
                    </div>
                  </div>
                </template>
              </article>
            </div>
            <p v-if="breadth.data.value.validity === 'stale'" class="breadth-notice">
              当前快照仍可追溯，但 SPY 持仓代理已超过 7 个日历日未更新，请先完成离线同步。
            </p>
            <p
              v-else-if="breadth.data.value.validity === 'insufficient_coverage'"
              class="breadth-notice"
            >
              覆盖率低于 90% 的指标不会返回数值；页面不会使用零值或其他价格序列补齐。
            </p>
          </template>
        </section>
      </template>

      <template v-else-if="activeView === 'sectors'">
        <DataState v-if="sectors.isPending.value" state="loading" />
        <DataState
          v-else-if="sectors.isError.value"
          state="error"
          :detail="sectors.error.value?.message"
          retry-label="重新读取板块快照"
          @retry="retrySectors"
        />
        <DataState
          v-else-if="sectors.data.value?.source_state !== 'available'"
          :state="sectors.data.value?.source_state || 'empty'"
          title="板块价格快照不可用"
          detail="板块视图只接受最近一次完整发布的快照。"
        />
        <DataState
          v-else-if="sectors.data.value.items.length === 0"
          state="empty"
          title="没有板块指标"
          detail="完整快照存在，但没有板块实体。"
        />
        <template v-else-if="sectors.data.value">
          <div class="table-intro">
            <div>
              <p class="eyebrow">Cross-sectional matrix</p>
              <h3>相对 SPY 强弱</h3>
            </div>
            <p>色阶只辅助识别方向，单元格始终保留数值和历史有效性。</p>
          </div>
          <DataTable caption="11 个标准板块的价格相对强弱" min-width="760px">
            <thead>
              <tr>
                <th>板块 / ETF</th>
                <th>20 日相对强弱</th>
                <th>60 日相对强弱</th>
                <th>数据日期</th>
                <th aria-label="详情"></th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="sector in sectors.data.value.items" :key="sector.sector_id">
                <td>
                  <button class="entity-link" type="button" @click="openSector(sector.sector_id)">
                    <span>
                      <strong>{{ sectorName(sector.sector_id) }}</strong>
                      <small class="mono">{{ sector.instrument_id }}</small>
                    </span>
                  </button>
                </td>
                <td :class="['heat-cell', metricCellTone(sector.relative_strength_20)]">
                  <RadarMetricValue :metric="sector.relative_strength_20" compact />
                </td>
                <td :class="['heat-cell', metricCellTone(sector.relative_strength_60)]">
                  <RadarMetricValue :metric="sector.relative_strength_60" compact />
                </td>
                <td class="tabular">{{ sectors.data.value.as_of_date || '—' }}</td>
                <td class="align-right">
                  <button
                    class="row-action"
                    type="button"
                    :aria-label="`查看 ${sectorName(sector.sector_id)} 详情`"
                    @click="openSector(sector.sector_id)"
                  >
                    <ChevronRight :size="17" aria-hidden="true" />
                  </button>
                </td>
              </tr>
            </tbody>
          </DataTable>
        </template>
      </template>

      <template v-else>
        <form class="stock-filters" @submit.prevent="applySearch">
          <label class="search-field">
            <span>搜索 watchlist</span>
            <div>
              <Search :size="16" aria-hidden="true" /><input
                v-model="searchDraft"
                type="search"
                maxlength="64"
                placeholder="AAPL 或 AAPL.US"
              />
            </div>
          </label>
          <label>
            <span>板块</span>
            <select :value="sectorFilter" @change="changeSectorFilter">
              <option value="">全部板块</option>
              <option
                v-for="sector in sectorOptions"
                :key="sector.sector_id"
                :value="sector.sector_id"
              >
                {{ sectorName(sector.sector_id) }}
              </option>
            </select>
          </label>
          <label>
            <span>排序</span>
            <select :value="stockSort" @change="changeSort">
              <option value="instrument">标的</option>
              <option value="momentum">126–21 动量</option>
              <option value="relative_momentum">板块相对动量</option>
              <option value="volatility">20 日波动率</option>
              <option value="drawdown">126 日回撤</option>
            </select>
          </label>
          <label>
            <span>方向</span>
            <select :value="sortDirection" @change="changeDirection">
              <option value="asc">升序</option>
              <option value="desc">降序</option>
            </select>
          </label>
          <button class="apply-filter" type="submit">应用搜索</button>
          <button
            v-if="
              stockSearch || sectorFilter || stockSort !== 'instrument' || sortDirection !== 'asc'
            "
            class="clear-filter"
            type="button"
            @click="clearStockFilters"
          >
            清除筛选
          </button>
        </form>

        <div class="dimension-note">
          <ArrowDownUp :size="17" aria-hidden="true" />
          <p>
            当前只具备价格趋势与风险维度。盈利修正、质量和估值将在有真实数据后独立展示，不填充占位分数。
          </p>
        </div>

        <DataState v-if="stocks.isPending.value" state="loading" />
        <DataState
          v-else-if="stocks.isError.value"
          state="error"
          :detail="stocks.error.value?.message"
          retry-label="重新读取个股快照"
          @retry="retryStocks"
        />
        <DataState
          v-else-if="stocks.data.value?.source_state !== 'available'"
          :state="stocks.data.value?.source_state || 'empty'"
          title="个股价格快照不可用"
          detail="watchlist 不从账户持仓自动扩充，也不会读取不完整同步结果。"
        />
        <DataState
          v-else-if="stocks.data.value.items.length === 0"
          state="empty"
          title="没有匹配的 watchlist 标的"
          detail="请调整搜索或板块筛选条件。"
        />
        <template v-else-if="stocks.data.value">
          <DataTable caption="watchlist 个股趋势与风险" min-width="1480px">
            <thead>
              <tr>
                <th>标的 / 板块</th>
                <th>126–21 动量</th>
                <th>相对板块</th>
                <th>距 MA200</th>
                <th>20 日波动率</th>
                <th>126 日最大回撤</th>
                <th>ATR20 / Price</th>
                <th aria-label="详情"></th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="stock in stocks.data.value.items" :key="stock.instrument_id">
                <td>
                  <button class="entity-link" type="button" @click="openStock(stock.instrument_id)">
                    <span>
                      <strong>{{ stock.symbol }}</strong>
                      <small
                        >{{ sectorName(stock.sector_id) }} ·
                        <span class="mono">{{ stock.instrument_id }}</span></small
                      >
                    </span>
                  </button>
                </td>
                <td><RadarMetricValue :metric="stock.momentum_126_21" compact /></td>
                <td>
                  <RadarMetricValue :metric="stock.sector_relative_momentum_126_21" compact />
                </td>
                <td><RadarMetricValue :metric="stock.distance_ma_200" compact /></td>
                <td><RadarMetricValue :metric="stock.realized_volatility_20" compact /></td>
                <td><RadarMetricValue :metric="stock.max_drawdown_126" compact /></td>
                <td><RadarMetricValue :metric="stock.atr_20_ratio" compact /></td>
                <td class="align-right">
                  <button
                    class="row-action"
                    type="button"
                    :aria-label="`查看 ${stock.symbol} 详情`"
                    @click="openStock(stock.instrument_id)"
                  >
                    <ChevronRight :size="17" aria-hidden="true" />
                  </button>
                </td>
              </tr>
            </tbody>
          </DataTable>
          <PaginationControls
            :offset="stocks.data.value.offset"
            :limit="stocks.data.value.limit"
            :item-count="stocks.data.value.items.length"
            :has-more="stocks.data.value.has_more"
            @change="changeOffset"
          />
        </template>
      </template>
    </section>

    <SideDrawer
      :open="selectedSectorId.length > 0"
      :title="sectorName(selectedSectorId)"
      description="当前完整价格快照中的板块详情"
      @close="closeSector"
    >
      <DataState v-if="sectorDetail.isPending.value" state="loading" />
      <DataState
        v-else-if="sectorDetail.isError.value"
        state="error"
        :detail="sectorDetail.error.value?.message"
      />
      <template v-else-if="sectorDetail.data.value">
        <div class="drawer-identity">
          <span>板块代理 ETF</span>
          <strong class="mono">{{ sectorDetail.data.value.instrument_id }}</strong>
        </div>
        <div class="drawer-metrics">
          <article>
            <span>20 日相对强弱</span
            ><RadarMetricValue :metric="sectorDetail.data.value.relative_strength_20" />
          </article>
          <article>
            <span>60 日相对强弱</span
            ><RadarMetricValue :metric="sectorDetail.data.value.relative_strength_60" />
          </article>
        </div>
        <div class="unavailable-block">
          <CircleOff :size="18" aria-hidden="true" />
          <div>
            <strong>宽度与 EPS 修正尚不可用</strong>
            <p>当前阶段没有 PIT 成分和盈利预期快照，因此不生成排名或历史走势。</p>
          </div>
        </div>
      </template>
    </SideDrawer>

    <SideDrawer
      :open="selectedInstrumentId.length > 0"
      :title="stockDetail.data.value?.symbol || selectedInstrumentId"
      description="watchlist 当前价格趋势与风险；不构成交易建议"
      width="620px"
      @close="closeStock"
    >
      <DataState v-if="stockDetail.isPending.value" state="loading" />
      <DataState
        v-else-if="stockDetail.isError.value"
        state="error"
        :detail="stockDetail.error.value?.message"
      />
      <template v-else-if="stockDetail.data.value">
        <div class="drawer-identity">
          <span>{{ sectorName(stockDetail.data.value.sector_id) }}</span>
          <strong class="mono">{{ stockDetail.data.value.instrument_id }}</strong>
        </div>
        <div class="drawer-section-title"><span>趋势</span><small>规范 INTERNAL 日线</small></div>
        <div class="drawer-metrics three-columns">
          <article>
            <span>126–21 动量</span
            ><RadarMetricValue :metric="stockDetail.data.value.momentum_126_21" />
          </article>
          <article>
            <span>相对板块</span
            ><RadarMetricValue :metric="stockDetail.data.value.sector_relative_momentum_126_21" />
          </article>
          <article>
            <span>距 MA200</span
            ><RadarMetricValue :metric="stockDetail.data.value.distance_ma_200" />
          </article>
        </div>
        <div class="drawer-section-title"><span>风险</span><small>独立展示，不合成分数</small></div>
        <div class="drawer-metrics three-columns">
          <article>
            <span>20 日波动率</span
            ><RadarMetricValue :metric="stockDetail.data.value.realized_volatility_20" />
          </article>
          <article>
            <span>126 日回撤</span
            ><RadarMetricValue :metric="stockDetail.data.value.max_drawdown_126" />
          </article>
          <article>
            <span>ATR20 / Price</span
            ><RadarMetricValue :metric="stockDetail.data.value.atr_20_ratio" />
          </article>
        </div>
        <div class="unavailable-block">
          <CircleOff :size="18" aria-hidden="true" />
          <div>
            <strong>修正、质量与估值尚不可用</strong>
            <p>R5 尚未采集可靠的盈利和基本面数据；页面不会使用价格指标替代这些维度。</p>
          </div>
        </div>
      </template>
    </SideDrawer>
  </div>
</template>

<style scoped>
.radar-workspace {
  min-height: 620px;
}

.radar-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  padding: 24px;
}

.radar-toolbar h2,
.table-intro h3,
.price-hero h3 {
  margin: 0;
  font-size: 1.08rem;
  letter-spacing: -0.025em;
}

.snapshot-strip {
  display: grid;
  align-items: center;
  gap: 18px 30px;
  padding: 16px 24px;
  background: var(--color-surface-soft);
  border-top: 1px solid var(--color-line);
  border-bottom: 1px solid var(--color-line);
  grid-template-columns: repeat(3, auto) minmax(280px, 1fr);
}

.snapshot-strip div {
  display: grid;
  gap: 5px;
}

.snapshot-strip span,
.drawer-identity span,
.drawer-metrics article > span {
  color: var(--color-text-faint);
  font-size: 0.67rem;
}

.snapshot-strip strong {
  font-size: 0.75rem;
}

.snapshot-strip p {
  display: flex;
  align-items: center;
  justify-self: end;
  gap: 7px;
  margin: 0;
  color: var(--color-text-soft);
  font-size: 0.72rem;
}

.radar-workspace > :deep(.data-state) {
  margin: 24px;
}

.module-grid {
  display: grid;
  gap: 12px;
  padding: 24px;
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.module-card {
  min-height: 132px;
  padding: 18px;
  background: var(--color-surface-soft);
  border: 1px solid var(--color-line);
  border-radius: var(--radius-md);
}

.module-card > div {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.module-card > div > span {
  font-size: 0.8rem;
  font-weight: 680;
}

.module-card p {
  margin: 23px 0 0;
  color: var(--color-text-soft);
  font-size: 0.73rem;
  line-height: 1.55;
}

.price-summary {
  display: grid;
  margin: 0 24px 24px;
  overflow: hidden;
  border: 1px solid var(--color-line);
  border-radius: var(--radius-lg);
  grid-template-columns: minmax(320px, 0.9fr) minmax(520px, 1.1fr);
}

.price-hero {
  display: grid;
  align-items: center;
  gap: 18px;
  padding: 26px;
  background: linear-gradient(145deg, #111216, #252730);
  grid-template-columns: auto minmax(0, 1fr) auto;
}

.radar-orbit {
  display: grid;
  width: 54px;
  height: 54px;
  color: #fff;
  background: var(--gradient-brand);
  border-radius: 18px;
  place-items: center;
}

.price-hero h3,
.price-hero p {
  color: #fff;
}

.price-hero p:last-child {
  margin: 8px 0 0;
  color: rgb(255 255 255 / 62%);
  font-size: 0.71rem;
  line-height: 1.5;
}

.coverage-value {
  display: grid;
  color: #fff;
  font-size: 1.55rem;
  text-align: right;
  letter-spacing: -0.04em;
}

.coverage-value small {
  margin-top: 5px;
  color: rgb(255 255 255 / 56%);
  font-size: 0.62rem;
  font-weight: 550;
  letter-spacing: 0;
}

.market-metric-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.market-metric-grid article {
  display: grid;
  align-content: center;
  gap: 15px;
  min-height: 150px;
  padding: 22px;
  border-left: 1px solid var(--color-line);
}

.market-metric-grid article > span {
  color: var(--color-text-soft);
  font-size: 0.72rem;
  font-weight: 620;
}

.macro-section {
  margin: 0 24px 24px;
  overflow: hidden;
  background: var(--color-surface);
  border: 1px solid var(--color-line);
  border-radius: var(--radius-lg);
}

.macro-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 24px;
  padding: 24px;
  border-bottom: 1px solid var(--color-line);
}

.macro-header h3 {
  margin: 3px 0 0;
  font-size: 1.08rem;
  letter-spacing: -0.025em;
}

.macro-header > div > p:last-child {
  max-width: 720px;
  margin: 8px 0 0;
  color: var(--color-text-soft);
  font-size: 0.73rem;
  line-height: 1.55;
}

.macro-section > :deep(.data-state) {
  margin: 20px;
}

.macro-meta {
  display: grid;
  gap: 18px 24px;
  padding: 18px 24px;
  background: var(--color-surface-soft);
  border-bottom: 1px solid var(--color-line);
  grid-template-columns: repeat(4, minmax(0, 1fr));
}

.macro-meta > div {
  display: grid;
  gap: 5px;
  min-width: 0;
}

.macro-meta span,
.macro-axis-grid span {
  color: var(--color-text-faint);
  font-size: 0.66rem;
}

.macro-meta strong {
  overflow-wrap: anywhere;
  font-size: 0.75rem;
}

.macro-layout {
  display: grid;
  grid-template-columns: minmax(280px, 0.72fr) minmax(0, 1.28fr);
}

.macro-reading {
  min-width: 0;
  padding: 28px;
  border-right: 1px solid var(--color-line);
}

.macro-current {
  position: relative;
  display: grid;
  gap: 8px;
  padding: 22px;
  overflow: hidden;
  color: #fff;
  background: var(--color-surface-strong);
  border-radius: var(--radius-md);
}

.macro-current::before {
  position: absolute;
  top: -35px;
  right: -25px;
  width: 125px;
  height: 100px;
  content: '';
  background: var(--gradient-brand);
  border-radius: 50%;
  filter: blur(42px);
  opacity: 0.52;
}

.macro-current span,
.macro-current strong,
.macro-current small {
  position: relative;
}

.macro-current span {
  color: rgb(255 255 255 / 56%);
  font-size: 0.67rem;
}

.macro-current strong {
  font-size: clamp(1.55rem, 2.3vw, 2.3rem);
  font-weight: 620;
  letter-spacing: -0.045em;
}

.macro-current small {
  color: rgb(255 255 255 / 62%);
  font-size: 0.66rem;
}

.macro-axis-grid {
  display: grid;
  gap: 0;
  margin-top: 18px;
  border: 1px solid var(--color-line);
  border-radius: var(--radius-md);
}

.macro-axis-grid article {
  display: grid;
  gap: 7px;
  padding: 17px;
  border-top: 1px solid var(--color-line);
}

.macro-axis-grid article:first-child {
  border-top: 0;
}

.macro-axis-grid strong {
  font-size: 1.35rem;
  font-weight: 620;
  letter-spacing: -0.035em;
}

.macro-axis-grid small,
.macro-provenance {
  color: var(--color-text-faint);
  font-size: 0.64rem;
  line-height: 1.5;
}

.macro-provenance {
  margin: 17px 2px 0;
}

.macro-visual {
  display: grid;
  min-width: 0;
  min-height: 420px;
  align-items: center;
}

.macro-notice {
  margin: 0;
  padding: 13px 24px;
  color: var(--color-warning);
  font-size: 0.7rem;
  line-height: 1.5;
  background: var(--color-warning-bg);
  border-top: 1px solid color-mix(in srgb, var(--color-warning) 18%, transparent);
}

.breadth-section {
  margin: 0 24px 24px;
  overflow: hidden;
  background: var(--color-surface);
  border: 1px solid var(--color-line);
  border-radius: var(--radius-lg);
}

.breadth-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 24px;
  padding: 24px;
  border-bottom: 1px solid var(--color-line);
}

.breadth-header h3 {
  margin: 3px 0 0;
  font-size: 1.08rem;
  letter-spacing: -0.025em;
}

.breadth-header > div > p:last-child {
  max-width: 720px;
  margin: 8px 0 0;
  color: var(--color-text-soft);
  font-size: 0.73rem;
  line-height: 1.55;
}

.breadth-section > :deep(.data-state) {
  margin: 20px;
}

.breadth-meta {
  display: grid;
  gap: 18px 24px;
  padding: 18px 24px;
  background: var(--color-surface-soft);
  border-bottom: 1px solid var(--color-line);
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.breadth-meta > div {
  display: grid;
  gap: 5px;
  min-width: 0;
}

.breadth-meta span,
.breadth-coverage span {
  color: var(--color-text-faint);
  font-size: 0.66rem;
}

.breadth-meta strong {
  overflow-wrap: anywhere;
  font-size: 0.75rem;
}

.breadth-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
}

.breadth-card {
  min-width: 0;
  padding: 22px;
  border-left: 1px solid var(--color-line);
}

.breadth-card:first-child {
  border-left: 0;
}

.breadth-card--partial,
.breadth-card--insufficient_coverage {
  background: color-mix(in srgb, var(--color-warning-bg) 42%, transparent);
}

.breadth-card-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
}

.breadth-card-heading > div {
  display: grid;
  gap: 5px;
}

.breadth-card-heading > div > span {
  font-size: 0.8rem;
  font-weight: 720;
  letter-spacing: 0.03em;
}

.breadth-card-heading small {
  color: var(--color-text-soft);
  font-size: 0.65rem;
}

.breadth-value {
  display: block;
  margin: 30px 0 27px;
  font-size: clamp(2rem, 3vw, 3.1rem);
  font-weight: 650;
  line-height: 1;
  letter-spacing: -0.06em;
}

.breadth-coverage {
  display: grid;
  gap: 9px;
}

.breadth-coverage > div {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
}

.breadth-coverage strong {
  font-size: 0.69rem;
}

.breadth-notice {
  margin: 0;
  padding: 13px 24px;
  color: var(--color-warning);
  font-size: 0.7rem;
  line-height: 1.5;
  background: var(--color-warning-bg);
  border-top: 1px solid color-mix(in srgb, var(--color-warning) 18%, transparent);
}

.table-intro {
  display: flex;
  align-items: end;
  justify-content: space-between;
  gap: 20px;
  padding: 24px;
}

.table-intro > p {
  max-width: 440px;
  margin: 0;
  color: var(--color-text-faint);
  font-size: 0.72rem;
  line-height: 1.5;
  text-align: right;
}

.entity-link {
  padding: 0;
  cursor: pointer;
  background: transparent;
  text-align: left;
}

.entity-link span {
  display: grid;
  gap: 5px;
}

.entity-link strong {
  font-size: 0.79rem;
}

.entity-link small {
  color: var(--color-text-faint);
  font-size: 0.67rem;
}

.heat-cell {
  box-shadow: inset 3px 0 transparent;
}

.heat-positive {
  background: rgb(20 122 84 / 4%);
  box-shadow: inset 3px 0 rgb(20 122 84 / 42%);
}

.heat-positive-strong {
  background: rgb(20 122 84 / 9%);
  box-shadow: inset 3px 0 var(--color-positive);
}

.heat-negative {
  background: rgb(180 58 67 / 4%);
  box-shadow: inset 3px 0 rgb(180 58 67 / 42%);
}

.heat-negative-strong {
  background: rgb(180 58 67 / 9%);
  box-shadow: inset 3px 0 var(--color-negative);
}

.heat-unavailable {
  background: var(--color-surface-soft);
}

.row-action {
  display: inline-grid;
  width: 32px;
  height: 32px;
  color: var(--color-text-soft);
  cursor: pointer;
  background: var(--color-surface-soft);
  border: 1px solid var(--color-line);
  border-radius: 10px;
  place-items: center;
}

.row-action:hover {
  color: var(--color-text);
  border-color: var(--color-line-strong);
}

.stock-filters {
  display: grid;
  align-items: end;
  gap: 12px;
  padding: 20px 24px;
  background: var(--color-surface-soft);
  border-top: 1px solid var(--color-line);
  border-bottom: 1px solid var(--color-line);
  grid-template-columns: minmax(220px, 1fr) repeat(3, minmax(130px, auto)) auto auto;
}

.stock-filters label {
  display: grid;
  gap: 7px;
}

.stock-filters label > span {
  color: var(--color-text-faint);
  font-size: 0.65rem;
  font-weight: 650;
}

.stock-filters input,
.stock-filters select {
  width: 100%;
  min-height: 38px;
  padding: 0 11px;
  background: #fff;
  border: 1px solid var(--color-line-strong);
  border-radius: 10px;
}

.search-field > div {
  position: relative;
}

.search-field svg {
  position: absolute;
  top: 11px;
  left: 11px;
  color: var(--color-text-faint);
}

.search-field input {
  padding-left: 35px;
}

.apply-filter,
.clear-filter {
  min-height: 38px;
  padding: 0 13px;
  font-size: 0.7rem;
  font-weight: 650;
  cursor: pointer;
  border-radius: 10px;
}

.apply-filter {
  color: #fff;
  background: var(--color-surface-strong);
}

.clear-filter {
  background: #fff;
  border: 1px solid var(--color-line-strong);
}

.dimension-note {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 13px 24px;
  color: var(--color-text-soft);
  border-bottom: 1px solid var(--color-line);
}

.dimension-note p {
  margin: 0;
  font-size: 0.72rem;
  line-height: 1.5;
}

.drawer-identity {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 15px 17px;
  margin-bottom: 18px;
  background: var(--color-surface-soft);
  border-radius: var(--radius-sm);
}

.drawer-identity strong {
  font-size: 0.78rem;
}

.drawer-metrics {
  display: grid;
  gap: 10px;
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.drawer-metrics.three-columns {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.drawer-metrics article {
  display: grid;
  gap: 15px;
  min-height: 112px;
  padding: 16px;
  background: var(--color-surface-soft);
  border: 1px solid var(--color-line);
  border-radius: var(--radius-sm);
}

.drawer-section-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin: 22px 0 10px;
}

.drawer-section-title span {
  font-size: 0.8rem;
  font-weight: 680;
}

.drawer-section-title small {
  color: var(--color-text-faint);
  font-size: 0.65rem;
}

.unavailable-block {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 17px;
  margin-top: 18px;
  color: var(--color-text-soft);
  background: var(--color-surface-soft);
  border: 1px dashed var(--color-line-strong);
  border-radius: var(--radius-sm);
}

.unavailable-block strong {
  color: var(--color-text);
  font-size: 0.76rem;
}

.unavailable-block p {
  margin: 6px 0 0;
  font-size: 0.7rem;
  line-height: 1.5;
}

@media (max-width: 1180px) {
  .price-summary {
    grid-template-columns: 1fr;
  }

  .market-metric-grid article:first-child {
    border-left: 0;
  }

  .breadth-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .macro-layout {
    grid-template-columns: 1fr;
  }

  .macro-reading {
    border-right: 0;
    border-bottom: 1px solid var(--color-line);
  }

  .breadth-card:nth-child(3) {
    border-left: 0;
  }

  .breadth-card:nth-child(n + 3) {
    border-top: 1px solid var(--color-line);
  }

  .stock-filters {
    grid-template-columns: repeat(3, 1fr);
  }

  .search-field {
    grid-column: span 3;
  }
}

@media (max-width: 860px) {
  .radar-toolbar,
  .table-intro {
    align-items: stretch;
    flex-direction: column;
  }

  .snapshot-strip {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .snapshot-strip p {
    justify-self: start;
    grid-column: 1 / -1;
  }

  .module-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .breadth-meta {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .macro-meta {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .table-intro > p {
    text-align: left;
  }

  .drawer-metrics.three-columns {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 620px) {
  .radar-toolbar,
  .module-grid,
  .table-intro {
    padding: 19px;
  }

  .radar-toolbar :deep(.segmented-tabs) {
    width: 100%;
    overflow-x: auto;
  }

  .radar-toolbar :deep(.segmented-tabs button) {
    flex: 1 0 auto;
  }

  .snapshot-strip,
  .module-grid,
  .market-metric-grid,
  .stock-filters,
  .drawer-metrics {
    grid-template-columns: 1fr;
  }

  .breadth-header,
  .macro-header {
    align-items: stretch;
    flex-direction: column;
    padding: 19px;
  }

  .breadth-section,
  .macro-section {
    margin: 0 19px 19px;
  }

  .breadth-meta,
  .breadth-grid,
  .macro-meta {
    grid-template-columns: 1fr;
  }

  .breadth-meta {
    padding: 18px 19px;
  }

  .macro-meta {
    padding: 18px 19px;
  }

  .macro-reading {
    padding: 19px;
  }

  .breadth-card,
  .breadth-card:nth-child(3) {
    border-top: 1px solid var(--color-line);
    border-left: 0;
  }

  .breadth-card:first-child {
    border-top: 0;
  }

  .search-field {
    grid-column: auto;
  }

  .price-summary {
    margin: 0 19px 19px;
  }

  .price-hero {
    grid-template-columns: auto minmax(0, 1fr);
  }

  .coverage-value {
    text-align: left;
    grid-column: 1 / -1;
  }

  .market-metric-grid article {
    border-top: 1px solid var(--color-line);
    border-left: 0;
  }
}
</style>

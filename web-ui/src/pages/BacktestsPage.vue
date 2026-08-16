<script setup lang="ts">
import { BarChart3, CalendarRange, FlaskConical, TimerReset } from '@lucide/vue'
import { useQuery } from '@tanstack/vue-query'
import { computed, watch } from 'vue'
import { useRoute, useRouter, type LocationQueryRaw } from 'vue-router'

import { backtestQuery, backtestsQuery, backtestTableQuery } from '../api/queries'
import type { BacktestQuery, BacktestTableName, ReportTableQuery } from '../api/types'
import DataChart from '../components/DataChart.vue'
import DataState from '../components/DataState.vue'
import DynamicReportTable from '../components/DynamicReportTable.vue'
import MetricCard from '../components/MetricCard.vue'
import PaginationControls from '../components/PaginationControls.vue'
import SegmentedTabs from '../components/SegmentedTabs.vue'
import StatusPill from '../components/StatusPill.vue'
import {
  formatCurrency,
  formatDateTime,
  formatLabel,
  formatNumber,
  formatPercent,
  formatScalar,
  formatSourceState,
  formatStatus,
  formatStrategyName,
} from '../utils/format'
import { extractEquitySeries } from '../utils/report'
import {
  PAGE_SIZE,
  REPORT_PAGE_SIZE,
  pageOffset,
  readPage,
  readQueryText,
  withPage,
} from '../utils/pagination'

const reportNames: readonly BacktestTableName[] = [
  'equity',
  'orders',
  'fills',
  'positions',
  'account',
]
const reportLabels: Readonly<Record<BacktestTableName, string>> = {
  equity: '权益',
  orders: '订单',
  fills: '成交',
  positions: '持仓',
  account: '账户',
}

const route = useRoute()
const router = useRouter()
const listPage = computed(() => readPage(route.query.page))
const listParams = computed<BacktestQuery>(() => ({
  offset: pageOffset(listPage.value),
  limit: PAGE_SIZE,
}))
const backtests = useQuery(computed(() => backtestsQuery(listParams.value)))
const selectedRunId = computed(() => readQueryText(route.query.run))
const detail = useQuery(
  computed(() => backtestQuery(selectedRunId.value, selectedRunId.value.length > 0)),
)

const availableReports = computed(() => {
  const available = new Set(detail.data.value?.available_tables ?? [])
  return reportNames.filter((name) => available.has(name))
})
const activeReport = computed<BacktestTableName>(() => {
  const requested = readQueryText(route.query.report)
  if (isReportName(requested) && availableReports.value.includes(requested)) {
    return requested
  }
  return availableReports.value[0] ?? 'equity'
})
const reportPage = computed(() => readPage(route.query.reportPage))
const reportParams = computed<ReportTableQuery>(() => ({
  offset: pageOffset(reportPage.value, REPORT_PAGE_SIZE),
  limit: REPORT_PAGE_SIZE,
}))
const report = useQuery(
  computed(() =>
    backtestTableQuery(
      selectedRunId.value,
      activeReport.value,
      reportParams.value,
      selectedRunId.value.length > 0 && availableReports.value.includes(activeReport.value),
    ),
  ),
)
const equity = useQuery(
  computed(() =>
    backtestTableQuery(
      selectedRunId.value,
      'equity',
      { offset: 0, limit: 500 },
      selectedRunId.value.length > 0 && availableReports.value.includes('equity'),
    ),
  ),
)
const equitySeries = computed(() =>
  equity.data.value ? extractEquitySeries(equity.data.value) : { points: [], error: null },
)
const reportTabs = computed(() =>
  availableReports.value.map((name) => ({ value: name, label: reportLabels[name] })),
)
const summaryEntries = computed(() => Object.entries(detail.data.value?.summary ?? {}))

watch(
  () => backtests.data.value?.items,
  (items) => {
    const first = items?.[0]
    if (!selectedRunId.value && first) {
      void router.replace({ query: { ...route.query, run: first.run_id } })
    }
  },
  { immediate: true },
)

function isReportName(value: string): value is BacktestTableName {
  return reportNames.some((name) => name === value)
}

function selectRun(runId: string): void {
  const query: LocationQueryRaw = { ...route.query, run: runId }
  delete query.report
  delete query.reportPage
  void router.replace({ query })
}

function changeListOffset(offset: number): void {
  const query = withPage(route.query, Math.floor(offset / PAGE_SIZE) + 1)
  delete query.run
  delete query.report
  delete query.reportPage
  void router.replace({ query })
}

function selectReport(value: string): void {
  if (!isReportName(value)) {
    return
  }
  const query: LocationQueryRaw = { ...route.query, report: value }
  delete query.reportPage
  if (value === availableReports.value[0]) {
    delete query.report
  }
  void router.replace({ query })
}

function changeReportOffset(offset: number): void {
  const query = { ...route.query }
  const page = Math.floor(offset / REPORT_PAGE_SIZE) + 1
  if (page <= 1) {
    delete query.reportPage
  } else {
    query.reportPage = String(page)
  }
  void router.replace({ query })
}

async function retryBacktests(): Promise<void> {
  await backtests.refetch()
}

async function retryDetail(): Promise<void> {
  await detail.refetch()
}

async function retryReport(): Promise<void> {
  await report.refetch()
}
</script>

<template>
  <div class="page-stack">
    <div class="page-intro">
      <div>
        <h1>回测中心</h1>
        <p>读取隔离的回测审计库与固定报告目录；页面不会启动回测、修改报告或复算交易结果。</p>
      </div>
      <div class="research-badge"><FlaskConical :size="15" aria-hidden="true" />只读研究账本</div>
    </div>

    <div class="backtest-layout">
      <aside class="surface run-browser">
        <header>
          <div>
            <p class="eyebrow">Runs</p>
            <h2>回测运行</h2>
          </div>
          <span v-if="backtests.data.value" class="page-count tabular">
            {{ backtests.data.value.items.length }} 条
          </span>
        </header>
        <DataState v-if="backtests.isPending.value" state="loading" />
        <DataState
          v-else-if="backtests.isError.value"
          state="error"
          :detail="backtests.error.value?.message"
          retry-label="重新读取回测"
          @retry="retryBacktests"
        />
        <DataState
          v-else-if="backtests.data.value?.items.length === 0"
          state="empty"
          title="暂无回测运行"
          detail="回测审计库与报告目录中都没有运行记录。"
        />
        <template v-else-if="backtests.data.value">
          <div class="run-list">
            <button
              v-for="run in backtests.data.value.items"
              :key="run.run_id"
              type="button"
              :class="{ active: selectedRunId === run.run_id }"
              :aria-pressed="selectedRunId === run.run_id"
              @click="selectRun(run.run_id)"
            >
              <span class="run-topline">
                <strong class="mono" :title="run.run_id">{{ run.run_id }}</strong>
                <StatusPill :status="run.status" :label="formatStatus(run.status)" />
              </span>
              <span>{{ formatStrategyName(run.strategy_name) }}</span>
              <span class="run-period">
                {{ run.evaluation_start || '—' }} → {{ run.end || '—' }}
              </span>
              <span class="run-return tabular">{{ formatPercent(run.annualized_return) }}</span>
            </button>
          </div>
          <PaginationControls
            :offset="backtests.data.value.offset"
            :limit="backtests.data.value.limit"
            :item-count="backtests.data.value.items.length"
            :has-more="backtests.data.value.has_more"
            @change="changeListOffset"
          />
        </template>
      </aside>

      <main class="detail-column">
        <DataState
          v-if="!selectedRunId"
          state="empty"
          title="选择一条回测"
          detail="从左侧运行列表选择需要检查的研究结果。"
        />
        <DataState v-else-if="detail.isPending.value" state="loading" />
        <DataState
          v-else-if="detail.isError.value"
          state="error"
          :detail="detail.error.value?.message"
          retry-label="重新读取详情"
          @retry="retryDetail"
        />
        <template v-else-if="detail.data.value">
          <section class="surface detail-hero">
            <div class="detail-title">
              <div class="detail-icon"><BarChart3 :size="22" aria-hidden="true" /></div>
              <div>
                <p class="eyebrow">Selected run</p>
                <h2 class="mono">{{ detail.data.value.run.run_id }}</h2>
                <p>{{ formatStrategyName(detail.data.value.run.strategy_name) }}</p>
              </div>
            </div>
            <div class="detail-status">
              <StatusPill
                :status="detail.data.value.run.status"
                :label="formatStatus(detail.data.value.run.status)"
              />
              <StatusPill
                :status="detail.data.value.run.report_state"
                :label="`报告 · ${formatSourceState(detail.data.value.run.report_state)}`"
              />
            </div>
            <dl>
              <div>
                <dt><CalendarRange :size="14" aria-hidden="true" />评估区间</dt>
                <dd>
                  {{ detail.data.value.run.evaluation_start || '—' }} →
                  {{ detail.data.value.run.end || '—' }}
                </dd>
              </div>
              <div>
                <dt><TimerReset :size="14" aria-hidden="true" />运行时间</dt>
                <dd>
                  {{ formatDateTime(detail.data.value.run.started_at_utc) }} →
                  {{ formatDateTime(detail.data.value.run.completed_at_utc) }}
                </dd>
              </div>
            </dl>
          </section>

          <section class="metric-grid" aria-label="回测绩效摘要">
            <MetricCard
              label="期末权益"
              :value="formatCurrency(detail.data.value.run.final_equity_usd, 'USD')"
              helper="正式评估区间末端"
              accent
            />
            <MetricCard
              label="年化收益"
              :value="formatPercent(detail.data.value.run.annualized_return)"
              helper="按报告交易日数年化"
            />
            <MetricCard
              label="最大回撤"
              :value="formatPercent(detail.data.value.run.max_drawdown)"
              helper="权益高水位回撤"
            />
            <MetricCard
              label="Sharpe"
              :value="formatNumber(detail.data.value.run.sharpe_ratio, 3)"
              helper="报告使用的无风险利率口径"
            />
          </section>

          <section v-if="summaryEntries.length" class="surface summary-card">
            <div class="section-header">
              <div>
                <p class="eyebrow">Summary payload</p>
                <h2>报告摘要</h2>
                <p>完整保留回测报告中允许公开的标量字段。</p>
              </div>
            </div>
            <dl>
              <div v-for="entry in summaryEntries" :key="entry[0]">
                <dt>{{ formatLabel(entry[0]) }}</dt>
                <dd class="tabular">{{ formatScalar(entry[1]) }}</dd>
              </div>
            </dl>
          </section>

          <section v-if="availableReports.includes('equity')" class="surface equity-card">
            <div class="section-header">
              <div>
                <p class="eyebrow">Equity curve</p>
                <h2>权益路径</h2>
                <p>最多读取报告前 500 行用于图表；完整记录仍可在下方逐页检查。</p>
              </div>
            </div>
            <DataState v-if="equity.isPending.value" state="loading" />
            <DataState
              v-else-if="equity.isError.value"
              state="error"
              :detail="equity.error.value?.message"
            />
            <DataState
              v-else-if="equitySeries.error"
              state="invalid"
              title="权益图无法生成"
              :detail="equitySeries.error"
            />
            <DataState
              v-else-if="equitySeries.points.length === 0"
              state="empty"
              title="权益报告为空"
              detail="报告存在，但没有权益数据行。"
            />
            <DataChart
              v-else
              title="回测权益曲线"
              kind="line"
              value-kind="currency"
              :points="equitySeries.points"
              :height="330"
            />
          </section>

          <section class="surface report-card">
            <div class="report-toolbar">
              <div>
                <p class="eyebrow">Native reports</p>
                <h2>回测报告表</h2>
              </div>
              <SegmentedTabs
                v-if="reportTabs.length"
                :model-value="activeReport"
                :tabs="reportTabs"
                label="回测报告类型"
                @update:model-value="selectReport"
              />
            </div>
            <DataState
              v-if="availableReports.length === 0"
              state="missing"
              title="没有可用报告表"
              detail="摘要存在，但五类固定 CSV 报告均未生成。"
            />
            <DataState v-else-if="report.isPending.value" state="loading" />
            <DataState
              v-else-if="report.isError.value"
              state="error"
              :detail="report.error.value?.message"
              retry-label="重新读取报告"
              @retry="retryReport"
            />
            <DataState
              v-else-if="report.data.value?.rows.length === 0"
              state="empty"
              title="当前报告页为空"
              detail="这张报告没有更多记录。"
            />
            <template v-else-if="report.data.value">
              <DynamicReportTable
                :table="report.data.value"
                :caption="`${reportLabels[activeReport]}报告`"
              />
              <PaginationControls
                :offset="report.data.value.offset"
                :limit="report.data.value.limit"
                :item-count="report.data.value.rows.length"
                :has-more="report.data.value.has_more"
                @change="changeReportOffset"
              />
            </template>
          </section>
        </template>
      </main>
    </div>
  </div>
</template>

<style scoped>
.research-badge {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 8px 11px;
  color: var(--color-brand-violet);
  font-size: 0.69rem;
  font-weight: 680;
  background: #f1edff;
  border-radius: 999px;
}

.backtest-layout {
  display: grid;
  align-items: start;
  gap: 18px;
  grid-template-columns: 330px minmax(0, 1fr);
}

.run-browser {
  position: sticky;
  top: 96px;
}

.run-browser > header,
.report-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 22px;
}

.run-browser h2,
.report-toolbar h2 {
  margin: 0;
  font-size: 1.04rem;
  letter-spacing: -0.02em;
}

.page-count {
  color: var(--color-text-faint);
  font-size: 0.68rem;
}

.run-browser > :deep(.data-state) {
  margin: 0 18px 18px;
}

.run-list {
  display: grid;
  border-top: 1px solid var(--color-line);
}

.run-list > button {
  position: relative;
  display: grid;
  gap: 7px;
  padding: 17px 20px;
  text-align: left;
  cursor: pointer;
  background: #fff;
  border-bottom: 1px solid var(--color-line);
}

.run-list > button:hover,
.run-list > button.active {
  background: var(--color-surface-soft);
}

.run-list > button.active::before {
  position: absolute;
  width: 3px;
  content: '';
  background: var(--gradient-brand);
  border-radius: 0 5px 5px 0;
  inset: 10px auto 10px 0;
}

.run-topline {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.run-topline strong {
  max-width: 180px;
  overflow: hidden;
  font-size: 0.76rem;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.run-list > button > span:not(.run-topline, .run-return) {
  color: var(--color-text-soft);
  font-size: 0.71rem;
}

.run-period {
  font-variant-numeric: tabular-nums;
}

.run-return {
  position: absolute;
  right: 20px;
  bottom: 17px;
  font-size: 0.76rem;
  font-weight: 680;
}

.detail-column {
  display: grid;
  gap: 18px;
  min-width: 0;
}

.detail-hero {
  display: grid;
  align-items: center;
  gap: 24px;
  padding: 25px;
  grid-template-columns: minmax(0, 1fr) auto;
}

.detail-title {
  display: flex;
  align-items: center;
  gap: 15px;
}

.detail-icon {
  display: grid;
  width: 48px;
  height: 48px;
  color: #fff;
  background: var(--color-surface-strong);
  border-radius: 16px;
  place-items: center;
}

.detail-title h2 {
  max-width: 560px;
  margin: 0;
  overflow: hidden;
  font-size: 1.18rem;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.detail-title p:last-child {
  margin: 6px 0 0;
  color: var(--color-text-soft);
  font-size: 0.74rem;
}

.detail-status {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 7px;
}

.detail-hero dl {
  display: grid;
  gap: 8px 24px;
  padding-top: 19px;
  margin: 0;
  border-top: 1px solid var(--color-line);
  grid-column: 1 / -1;
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.detail-hero dl div {
  display: grid;
  gap: 5px;
}

.detail-hero dt {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--color-text-faint);
  font-size: 0.65rem;
  text-transform: uppercase;
}

.detail-hero dd {
  margin: 0;
  font-size: 0.7rem;
  font-variant-numeric: tabular-nums;
}

.summary-card,
.equity-card {
  padding: 24px;
}

.summary-card dl {
  display: grid;
  gap: 1px;
  margin: 0;
  overflow: hidden;
  background: var(--color-line);
  border: 1px solid var(--color-line);
  border-radius: var(--radius-md);
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.summary-card dl div {
  display: grid;
  gap: 6px;
  min-width: 0;
  padding: 13px;
  background: var(--color-surface-soft);
}

.summary-card dt {
  color: var(--color-text-faint);
  font-size: 0.62rem;
}

.summary-card dd {
  margin: 0;
  overflow: hidden;
  font-size: 0.71rem;
  font-weight: 650;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.equity-card > :deep(.data-state) {
  min-height: 120px;
}

.report-card > :deep(.data-state) {
  margin: 0 22px 22px;
}

@media (max-width: 1160px) {
  .backtest-layout {
    grid-template-columns: 280px minmax(0, 1fr);
  }

  .summary-card dl {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 920px) {
  .backtest-layout {
    grid-template-columns: 1fr;
  }

  .run-browser {
    position: static;
  }

  .run-list {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 660px) {
  .run-list,
  .detail-hero dl,
  .summary-card dl {
    grid-template-columns: 1fr;
  }

  .detail-hero {
    grid-template-columns: 1fr;
  }

  .detail-status {
    justify-content: flex-start;
  }

  .report-toolbar {
    align-items: stretch;
    flex-direction: column;
  }

  .report-toolbar :deep(.segmented-tabs) {
    width: 100%;
    overflow-x: auto;
  }
}
</style>

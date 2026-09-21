<script setup lang="ts">
import { GitBranch, RadioTower, Scale, ShieldCheck } from '@lucide/vue'
import { useQuery } from '@tanstack/vue-query'
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter, type LocationQueryRaw } from 'vue-router'

import { signalsQuery, workflowQuery, workflowsQuery } from '../api/queries'
import type { SignalQuery, WorkflowQuery } from '../api/types'
import DataState from '../components/DataState.vue'
import DataTable from '../components/DataTable.vue'
import PaginationControls from '../components/PaginationControls.vue'
import SegmentedTabs from '../components/SegmentedTabs.vue'
import SideDrawer from '../components/SideDrawer.vue'
import StatusPill from '../components/StatusPill.vue'
import {
  formatCurrency,
  formatDateTime,
  formatDirection,
  formatLabel,
  formatPercent,
  formatQuantity,
  formatScalar,
  formatStatus,
  formatStrategyName,
} from '../utils/format'
import { PAGE_SIZE, pageOffset, readPage, readQueryText, withPage } from '../utils/pagination'

const route = useRoute()
const router = useRouter()
const activeTab = computed({
  get: () => (route.query.tab === 'signals' ? 'signals' : 'workflows'),
  set: (value: string) => {
    const query: LocationQueryRaw = { ...route.query }
    delete query.page
    delete query.event
    if (value === 'signals') {
      query.tab = 'signals'
    } else {
      delete query.tab
      delete query.instrument
    }
    void router.replace({ query })
  },
})
const page = computed(() => readPage(route.query.page))
const statusFilter = computed(() => readQueryText(route.query.status))
const instrumentFilter = computed(() => readQueryText(route.query.instrument))
const instrumentDraft = ref(instrumentFilter.value)
const selectedEventId = computed(() => readQueryText(route.query.event))

watch(instrumentFilter, (value) => {
  instrumentDraft.value = value
})

const workflowParams = computed<WorkflowQuery>(() => {
  const query: WorkflowQuery = { offset: pageOffset(page.value), limit: PAGE_SIZE }
  if (statusFilter.value) {
    query.status = statusFilter.value
  }
  return query
})
const signalParams = computed<SignalQuery>(() => {
  const query: SignalQuery = { offset: pageOffset(page.value), limit: PAGE_SIZE }
  if (statusFilter.value) {
    query.status = statusFilter.value
  }
  if (instrumentFilter.value) {
    query.instrument_id = instrumentFilter.value
  }
  return query
})
const workflows = useQuery(computed(() => workflowsQuery(workflowParams.value)))
const signals = useQuery(computed(() => signalsQuery(signalParams.value)))
const workflowDetail = useQuery(
  computed(() => workflowQuery(selectedEventId.value, selectedEventId.value.length > 0)),
)

const tabs = computed(() => [
  { value: 'workflows', label: '工作流', count: workflows.data.value?.items.length ?? 0 },
  { value: 'signals', label: '逐标的信号', count: signals.data.value?.items.length ?? 0 },
])

function updateFilter(name: 'status' | 'instrument', value: string): void {
  const query: LocationQueryRaw = { ...route.query }
  delete query.page
  if (name === 'status') {
    if (value) {
      query.status = value
    } else {
      delete query.status
    }
  } else if (value) {
    query.instrument = value
  } else {
    delete query.instrument
  }
  void router.replace({ query })
}

function changeStatus(event: Event): void {
  const target = event.target
  if (target instanceof HTMLSelectElement) {
    updateFilter('status', target.value)
  }
}

function applyInstrument(): void {
  updateFilter('instrument', instrumentDraft.value.trim())
}

function clearFilters(): void {
  instrumentDraft.value = ''
  const query: LocationQueryRaw = { ...route.query }
  delete query.status
  delete query.instrument
  delete query.page
  void router.replace({ query })
}

function changeOffset(offset: number): void {
  void router.replace({ query: withPage(route.query, Math.floor(offset / PAGE_SIZE) + 1) })
}

function openWorkflow(eventId: string): void {
  void router.replace({ query: { ...route.query, event: eventId } })
}

function closeWorkflow(): void {
  const query: LocationQueryRaw = { ...route.query }
  delete query.event
  void router.replace({ query })
}

async function retryWorkflows(): Promise<void> {
  await workflows.refetch()
}

async function retrySignals(): Promise<void> {
  await signals.refetch()
}
</script>

<template>
  <div class="page-stack">
    <div class="page-intro">
      <div>
        <h1>决策流</h1>
        <p>工作流是一整次交易意图，逐标的信号是策略输出；筛选、分页和详情对象均保存在当前 URL。</p>
      </div>
      <div class="read-only-badge"><RadioTower :size="15" aria-hidden="true" />只读审计视图</div>
    </div>

    <section v-if="workflows.data.value?.factor_decisions?.length" class="surface records-card">
      <div class="records-toolbar"><h2>因子检查与持仓保护</h2></div>
      <DataTable caption="因子检查记录" min-width="960px">
        <thead>
          <tr>
            <th>因子日期</th>
            <th>状态</th>
            <th>原因</th>
            <th>保护标的</th>
            <th>有效 / 候选</th>
            <th>模型发布</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="decision in workflows.data.value.factor_decisions" :key="decision.id">
            <td>{{ decision.asof_date }}</td>
            <td>
              {{
                decision.recovered_at ? '已恢复' : decision.status === 'SKIP' ? '已跳过' : '可调仓'
              }}
            </td>
            <td>{{ decision.reason }}</td>
            <td>{{ decision.preserve_positions.join(', ') || '无' }}</td>
            <td>{{ decision.eligible_count ?? '—' }} / {{ decision.candidate_count ?? '—' }}</td>
            <td class="mono">{{ decision.model_release_id || '—' }}</td>
          </tr>
        </tbody>
      </DataTable>
    </section>

    <section class="surface records-card">
      <div class="records-toolbar">
        <div>
          <p class="eyebrow">Trade intent</p>
          <h2>{{ activeTab === 'workflows' ? '工作流队列' : '策略信号' }}</h2>
        </div>
        <SegmentedTabs v-model="activeTab" :tabs="tabs" label="决策记录类型" />
      </div>

      <form class="filter-bar" @submit.prevent="applyInstrument">
        <label>
          <span>状态</span>
          <select :value="statusFilter" @change="changeStatus">
            <option value="">全部状态</option>
            <option value="NEW">待处理</option>
            <option value="PENDING_APPROVAL">待审批</option>
            <option value="APPROVED">已批准</option>
            <option value="DENIED">已否决</option>
            <option value="FILLED">已成交</option>
            <option value="FAILED">失败</option>
            <option value="EXPIRED">已过期</option>
          </select>
        </label>
        <label v-if="activeTab === 'signals'" class="instrument-filter">
          <span>标的 ID</span>
          <input
            v-model="instrumentDraft"
            type="search"
            maxlength="128"
            placeholder="例如 AAPL.US"
          />
        </label>
        <button v-if="activeTab === 'signals'" class="apply-filter" type="submit">应用标的</button>
        <button
          v-if="statusFilter || instrumentFilter"
          class="clear-filter"
          type="button"
          @click="clearFilters"
        >
          清除筛选
        </button>
      </form>

      <template v-if="activeTab === 'workflows'">
        <DataState v-if="workflows.isPending.value" state="loading" />
        <DataState
          v-else-if="workflows.isError.value"
          state="error"
          :detail="workflows.error.value?.message"
          retry-label="重新读取工作流"
          @retry="retryWorkflows"
        />
        <DataState
          v-else-if="workflows.data.value?.items.length === 0"
          state="empty"
          title="没有匹配的工作流"
          detail="当前筛选条件下没有策略工作流。"
        />
        <template v-else-if="workflows.data.value">
          <DataTable caption="工作流队列" min-width="1120px">
            <thead>
              <tr>
                <th>策略 / 事件</th>
                <th>信号时间</th>
                <th>状态</th>
                <th class="align-right">目标 / 订单</th>
                <th>风险摘要</th>
                <th>说明</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="workflow in workflows.data.value.items" :key="workflow.event_id">
                <td>
                  <button
                    class="row-trigger"
                    type="button"
                    @click="openWorkflow(workflow.event_id)"
                  >
                    <span class="cell-primary">{{
                      formatStrategyName(workflow.strategy_name)
                    }}</span>
                    <span class="cell-subtle mono" :title="workflow.event_id">{{
                      workflow.event_id
                    }}</span>
                  </button>
                </td>
                <td>
                  <div class="cell-stack">
                    <span class="cell-primary tabular">{{
                      formatDateTime(workflow.signal_timestamp_utc)
                    }}</span
                    ><span class="cell-subtle"
                      >截止 {{ formatDateTime(workflow.expires_at_utc) }}</span
                    >
                  </div>
                </td>
                <td>
                  <StatusPill :status="workflow.status" :label="formatStatus(workflow.status)" />
                </td>
                <td class="align-right tabular">
                  <strong class="cell-primary">{{ workflow.target_count }}</strong
                  ><span class="count-separator"> / </span>{{ workflow.planned_order_count }}
                </td>
                <td>
                  <span class="summary-text" :title="workflow.risk_summary || ''">{{
                    workflow.risk_summary || '—'
                  }}</span>
                </td>
                <td>
                  <span class="summary-text" :title="workflow.reason">{{ workflow.reason }}</span>
                </td>
              </tr>
            </tbody>
          </DataTable>
          <PaginationControls
            :offset="workflows.data.value.offset"
            :limit="workflows.data.value.limit"
            :item-count="workflows.data.value.items.length"
            :has-more="workflows.data.value.has_more"
            @change="changeOffset"
          />
        </template>
      </template>

      <template v-else>
        <DataState v-if="signals.isPending.value" state="loading" />
        <DataState
          v-else-if="signals.isError.value"
          state="error"
          :detail="signals.error.value?.message"
          retry-label="重新读取信号"
          @retry="retrySignals"
        />
        <DataState
          v-else-if="signals.data.value?.items.length === 0"
          state="empty"
          title="没有匹配的信号"
          detail="当前筛选条件下没有逐标的策略信号。"
        />
        <template v-else-if="signals.data.value">
          <DataTable caption="逐标的策略信号" min-width="1040px">
            <thead>
              <tr>
                <th>标的</th>
                <th>方向</th>
                <th class="align-right">目标权重</th>
                <th>策略</th>
                <th>状态</th>
                <th>信号时间</th>
                <th>说明</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="signal in signals.data.value.items"
                :key="`${signal.event_id}:${signal.instrument_id}`"
              >
                <td>
                  <div class="instrument-cell">
                    <span class="instrument-mark" aria-hidden="true">{{
                      signal.instrument_id.slice(0, 1).toUpperCase()
                    }}</span>
                    <div class="cell-stack">
                      <span class="cell-primary">{{ signal.instrument_id }}</span
                      ><span class="cell-subtle mono" :title="signal.event_id">{{
                        signal.event_id
                      }}</span>
                    </div>
                  </div>
                </td>
                <td>
                  <StatusPill
                    :status="signal.direction"
                    :label="formatDirection(signal.direction)"
                    :dot="false"
                  />
                </td>
                <td class="align-right cell-primary tabular">
                  {{ formatPercent(signal.target_weight) }}
                </td>
                <td>{{ formatStrategyName(signal.strategy_name) }}</td>
                <td><StatusPill :status="signal.status" :label="formatStatus(signal.status)" /></td>
                <td class="tabular">{{ formatDateTime(signal.timestamp_utc) }}</td>
                <td>
                  <span class="summary-text" :title="signal.reason">{{ signal.reason }}</span>
                </td>
              </tr>
            </tbody>
          </DataTable>
          <PaginationControls
            :offset="signals.data.value.offset"
            :limit="signals.data.value.limit"
            :item-count="signals.data.value.items.length"
            :has-more="signals.data.value.has_more"
            @change="changeOffset"
          />
        </template>
      </template>
    </section>

    <aside class="flow-explainer surface">
      <div class="flow-icon" aria-hidden="true"><GitBranch :size="19" :stroke-width="1.7" /></div>
      <div>
        <strong>唯一执行链路保持不变</strong>
        <p>信号 → ExecutionGateway → 应用风控 → 审批 → NautilusTrader → IBKR Paper。</p>
      </div>
    </aside>

    <SideDrawer
      :open="Boolean(selectedEventId)"
      :title="
        workflowDetail.data.value
          ? formatStrategyName(workflowDetail.data.value.workflow.strategy_name)
          : '工作流详情'
      "
      :description="selectedEventId"
      @close="closeWorkflow"
    >
      <DataState v-if="workflowDetail.isPending.value" state="loading" />
      <DataState
        v-else-if="workflowDetail.isError.value"
        state="error"
        :detail="workflowDetail.error.value?.message"
      />
      <template v-else-if="workflowDetail.data.value">
        <div class="drawer-status-row">
          <StatusPill
            :status="workflowDetail.data.value.workflow.status"
            :label="formatStatus(workflowDetail.data.value.workflow.status)"
          />
          <span>再平衡键 · {{ workflowDetail.data.value.workflow.rebalance_key }}</span>
        </div>

        <section class="drawer-section">
          <div class="drawer-section-title">
            <ShieldCheck :size="17" aria-hidden="true" />
            <div>
              <h3>交易意图</h3>
              <p>{{ workflowDetail.data.value.workflow.reason }}</p>
            </div>
          </div>
          <dl class="drawer-grid">
            <div>
              <dt>信号时间</dt>
              <dd>{{ formatDateTime(workflowDetail.data.value.workflow.signal_timestamp_utc) }}</dd>
            </div>
            <div>
              <dt>失效时间</dt>
              <dd>{{ formatDateTime(workflowDetail.data.value.workflow.expires_at_utc) }}</dd>
            </div>
            <div>
              <dt>风险摘要</dt>
              <dd>{{ workflowDetail.data.value.workflow.risk_summary || '—' }}</dd>
            </div>
            <div>
              <dt>计划订单</dt>
              <dd>{{ workflowDetail.data.value.workflow.planned_order_count }}</dd>
            </div>
            <template v-if="workflowDetail.data.value.factor_asof_date">
              <div>
                <dt>因子日期</dt>
                <dd>{{ workflowDetail.data.value.factor_asof_date }}</dd>
              </div>
              <div>
                <dt>最早执行</dt>
                <dd>
                  {{ formatDateTime(workflowDetail.data.value.not_before_utc ?? null) }}
                </dd>
              </div>
              <div>
                <dt>保护标的</dt>
                <dd>
                  {{ workflowDetail.data.value.preserve_positions?.join(', ') || '无' }}
                </dd>
              </div>
              <div>
                <dt>模型发布</dt>
                <dd class="mono">{{ workflowDetail.data.value.model_release_id }}</dd>
              </div>
            </template>
          </dl>
        </section>

        <section class="drawer-section">
          <div class="drawer-section-title">
            <Scale :size="17" aria-hidden="true" />
            <div>
              <h3>目标权重</h3>
              <p>策略事件携带的目标组合，不代表已经成交。</p>
            </div>
          </div>
          <div v-if="workflowDetail.data.value.target_weights.length" class="weight-list">
            <div v-for="weight in workflowDetail.data.value.target_weights" :key="weight[0]">
              <span>{{ weight[0] }}</span
              ><strong class="tabular">{{ formatPercent(weight[1]) }}</strong>
            </div>
          </div>
          <DataState v-else state="empty" title="没有目标权重" detail="该工作流没有逐标的目标。" />
        </section>

        <section v-if="workflowDetail.data.value.planned_orders.length" class="drawer-section">
          <div class="drawer-section-title">
            <div>
              <h3>计划订单</h3>
              <p>风控和审批之前形成的只读计划。</p>
            </div>
          </div>
          <dl
            v-for="(order, index) in workflowDetail.data.value.planned_orders"
            :key="index"
            class="planned-order"
          >
            <div v-for="(value, key) in order" :key="key">
              <dt>{{ formatLabel(key) }}</dt>
              <dd>{{ formatScalar(value) }}</dd>
            </div>
          </dl>
        </section>

        <section class="drawer-section">
          <div class="drawer-section-title">
            <div>
              <h3>审计时间线</h3>
              <p>信号、决定、订单和成交按 UTC 时间合并排序。</p>
            </div>
          </div>
          <ol v-if="workflowDetail.data.value.timeline.length" class="timeline">
            <li
              v-for="item in workflowDetail.data.value.timeline"
              :key="`${item.kind}:${item.timestamp_utc}:${item.title}`"
            >
              <span class="timeline-mark" aria-hidden="true"></span>
              <div>
                <div>
                  <strong>{{ item.title }}</strong
                  ><StatusPill :status="item.status" :label="formatStatus(item.status)" />
                </div>
                <p>{{ item.detail }}</p>
                <small>{{ formatDateTime(item.timestamp_utc) }}</small>
              </div>
            </li>
          </ol>
          <DataState
            v-else
            state="empty"
            title="没有时间线事件"
            detail="工作流存在，但没有可展示审计节点。"
          />
        </section>

        <section v-if="workflowDetail.data.value.fills.length" class="drawer-section">
          <div class="drawer-section-title">
            <div><h3>逐笔成交</h3></div>
          </div>
          <div class="fill-list">
            <div v-for="fill in workflowDetail.data.value.fills" :key="fill.trade_id">
              <span
                >{{ fill.instrument_id }} · {{ formatDirection(fill.direction) }}
                {{ formatQuantity(fill.quantity) }}</span
              ><strong>{{ formatCurrency(fill.price, 'USD') }}</strong
              ><small
                >{{ formatDateTime(fill.timestamp_utc) }} · 佣金
                {{ formatCurrency(fill.commission, 'USD') }}</small
              >
            </div>
          </div>
        </section>
      </template>
    </SideDrawer>
  </div>
</template>

<style scoped>
.read-only-badge {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 8px 11px;
  color: var(--color-positive);
  font-size: 0.7rem;
  font-weight: 650;
  white-space: nowrap;
  background: var(--color-positive-bg);
  border-radius: 999px;
}

.records-card {
  padding-top: 24px;
}
.records-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  padding: 0 24px 20px;
}
.records-toolbar h2 {
  margin: 0;
  font-size: 1.04rem;
  letter-spacing: -0.02em;
}
.records-card > :deep(.data-state) {
  margin: 0 24px 24px;
}

.filter-bar {
  display: flex;
  align-items: end;
  gap: 10px;
  padding: 15px 24px;
  background: var(--color-surface-soft);
  border-top: 1px solid var(--color-line);
}

.filter-bar label {
  display: grid;
  gap: 5px;
}
.filter-bar label > span {
  color: var(--color-text-faint);
  font-size: 0.61rem;
  font-weight: 700;
  text-transform: uppercase;
}
.filter-bar select,
.filter-bar input {
  min-height: 36px;
  padding: 0 11px;
  background: #fff;
  border: 1px solid var(--color-line-strong);
  border-radius: 10px;
}
.instrument-filter {
  width: min(250px, 100%);
}
.filter-bar button {
  min-height: 36px;
  padding: 0 12px;
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
  color: var(--color-text-soft);
  background: #fff;
  border: 1px solid var(--color-line);
}

.row-trigger {
  display: grid;
  gap: 4px;
  max-width: 220px;
  padding: 0;
  text-align: left;
  cursor: pointer;
  background: transparent;
}
.row-trigger:hover .cell-primary {
  color: var(--color-brand-blue);
}
.row-trigger .mono {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.count-separator {
  color: var(--color-line-strong);
}
.summary-text {
  display: block;
  max-width: 250px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.instrument-cell {
  display: flex;
  align-items: center;
  gap: 11px;
}
.instrument-mark {
  display: grid;
  flex: 0 0 auto;
  width: 30px;
  height: 30px;
  color: var(--color-brand-violet);
  font-size: 0.7rem;
  font-weight: 720;
  background: #f1edff;
  border-radius: 10px;
  place-items: center;
}
.instrument-cell .mono {
  max-width: 180px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.flow-explainer {
  display: flex;
  align-items: flex-start;
  gap: 14px;
  padding: 18px 20px;
}
.flow-icon {
  display: grid;
  flex: 0 0 auto;
  width: 38px;
  height: 38px;
  color: var(--color-brand-violet);
  background: #f1edff;
  border-radius: 12px;
  place-items: center;
}
.flow-explainer strong {
  font-size: 0.8rem;
}
.flow-explainer p {
  margin: 5px 0 0;
  color: var(--color-text-soft);
  font-size: 0.73rem;
  line-height: 1.5;
}

.drawer-status-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 14px;
  color: var(--color-text-soft);
  font-size: 0.67rem;
  background: var(--color-surface-soft);
  border-radius: var(--radius-md);
}
.drawer-section {
  padding: 23px 0;
  border-bottom: 1px solid var(--color-line);
}
.drawer-section:last-child {
  border-bottom: 0;
}
.drawer-section-title {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  margin-bottom: 15px;
}
.drawer-section-title h3 {
  margin: 0;
  font-size: 0.9rem;
}
.drawer-section-title p {
  margin: 5px 0 0;
  color: var(--color-text-soft);
  font-size: 0.7rem;
  line-height: 1.5;
}
.drawer-grid {
  display: grid;
  gap: 1px;
  margin: 0;
  overflow: hidden;
  background: var(--color-line);
  border: 1px solid var(--color-line);
  border-radius: 13px;
  grid-template-columns: repeat(2, minmax(0, 1fr));
}
.drawer-grid div {
  display: grid;
  gap: 5px;
  padding: 12px;
  background: var(--color-surface-soft);
}
.drawer-grid dt,
.planned-order dt {
  color: var(--color-text-faint);
  font-size: 0.61rem;
  text-transform: uppercase;
}
.drawer-grid dd,
.planned-order dd {
  margin: 0;
  font-size: 0.69rem;
  line-height: 1.45;
}
.weight-list {
  display: grid;
  gap: 7px;
}
.weight-list div {
  display: flex;
  justify-content: space-between;
  gap: 15px;
  padding: 10px 12px;
  font-size: 0.72rem;
  background: var(--color-surface-soft);
  border-radius: 10px;
}
.planned-order {
  display: grid;
  gap: 8px;
  padding: 13px;
  margin: 0 0 9px;
  background: var(--color-surface-soft);
  border-radius: 12px;
}
.planned-order div {
  display: flex;
  justify-content: space-between;
  gap: 12px;
}
.timeline {
  display: grid;
  gap: 0;
  padding: 0;
  margin: 0;
  list-style: none;
}
.timeline li {
  position: relative;
  display: grid;
  gap: 12px;
  padding: 0 0 22px 22px;
  grid-template-columns: 10px minmax(0, 1fr);
}
.timeline li::before {
  position: absolute;
  top: 9px;
  bottom: -9px;
  left: 4px;
  width: 1px;
  content: '';
  background: var(--color-line-strong);
}
.timeline li:last-child::before {
  display: none;
}
.timeline-mark {
  z-index: 1;
  width: 9px;
  height: 9px;
  margin-top: 6px;
  background: var(--color-brand-violet);
  border: 2px solid #fff;
  border-radius: 50%;
  box-shadow: 0 0 0 2px #dcd5ff;
}
.timeline li > div > div {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 9px;
}
.timeline strong {
  font-size: 0.75rem;
}
.timeline p {
  margin: 5px 0;
  color: var(--color-text-soft);
  font-size: 0.69rem;
  line-height: 1.5;
}
.timeline small {
  color: var(--color-text-faint);
  font-size: 0.62rem;
}
.fill-list {
  display: grid;
  gap: 8px;
}
.fill-list > div {
  display: grid;
  gap: 5px;
  padding: 12px;
  background: var(--color-surface-soft);
  border-radius: 11px;
  grid-template-columns: minmax(0, 1fr) auto;
}
.fill-list span,
.fill-list strong {
  font-size: 0.7rem;
}
.fill-list small {
  color: var(--color-text-faint);
  font-size: 0.62rem;
  grid-column: 1 / -1;
}

@media (max-width: 660px) {
  .records-toolbar {
    align-items: flex-start;
    flex-direction: column;
    padding: 0 19px 18px;
  }
  .records-toolbar :deep(.segmented-tabs) {
    width: 100%;
  }
  .records-toolbar :deep(.segmented-tabs button) {
    flex: 1;
    justify-content: center;
  }
  .filter-bar {
    align-items: stretch;
    flex-direction: column;
    padding: 14px 19px;
  }
  .instrument-filter {
    width: 100%;
  }
  .drawer-grid {
    grid-template-columns: 1fr;
  }
}
</style>

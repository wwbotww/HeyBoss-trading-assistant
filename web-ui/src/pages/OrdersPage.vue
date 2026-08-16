<script setup lang="ts">
import { Landmark, ReceiptText } from '@lucide/vue'
import { useQuery } from '@tanstack/vue-query'
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter, type LocationQueryRaw } from 'vue-router'

import { fillsQuery, orderQuery, ordersQuery } from '../api/queries'
import type { FillQuery, OrderQuery } from '../api/types'
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
  formatQuantity,
  formatStatus,
  formatStrategyName,
} from '../utils/format'
import { PAGE_SIZE, pageOffset, readPage, readQueryText, withPage } from '../utils/pagination'

const route = useRoute()
const router = useRouter()
const activeTab = computed({
  get: () => (route.query.tab === 'fills' ? 'fills' : 'orders'),
  set: (value: string) => {
    const query: LocationQueryRaw = { ...route.query }
    delete query.page
    delete query.order
    if (value === 'fills') {
      query.tab = 'fills'
      delete query.status
    } else {
      delete query.tab
    }
    void router.replace({ query })
  },
})
const page = computed(() => readPage(route.query.page))
const statusFilter = computed(() => readQueryText(route.query.status))
const instrumentFilter = computed(() => readQueryText(route.query.instrument))
const instrumentDraft = ref(instrumentFilter.value)
const selectedOrderId = computed(() => readQueryText(route.query.order))

watch(instrumentFilter, (value) => {
  instrumentDraft.value = value
})

const orderParams = computed<OrderQuery>(() => {
  const query: OrderQuery = { offset: pageOffset(page.value), limit: PAGE_SIZE }
  if (statusFilter.value) {
    query.status = statusFilter.value
  }
  if (instrumentFilter.value) {
    query.instrument_id = instrumentFilter.value
  }
  return query
})
const fillParams = computed<FillQuery>(() => {
  const query: FillQuery = { offset: pageOffset(page.value), limit: PAGE_SIZE }
  if (instrumentFilter.value) {
    query.instrument_id = instrumentFilter.value
  }
  return query
})
const orders = useQuery(computed(() => ordersQuery(orderParams.value)))
const fills = useQuery(computed(() => fillsQuery(fillParams.value)))
const orderDetail = useQuery(
  computed(() => orderQuery(selectedOrderId.value, selectedOrderId.value.length > 0)),
)

const tabs = computed(() => [
  { value: 'orders', label: '订单生命周期', count: orders.data.value?.items.length ?? 0 },
  { value: 'fills', label: '逐笔成交', count: fills.data.value?.items.length ?? 0 },
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

function openOrder(clientOrderId: string): void {
  void router.replace({ query: { ...route.query, order: clientOrderId } })
}

function closeOrder(): void {
  const query: LocationQueryRaw = { ...route.query }
  delete query.order
  void router.replace({ query })
}

async function retryOrders(): Promise<void> {
  await orders.refetch()
}

async function retryFills(): Promise<void> {
  await fills.refetch()
}
</script>

<template>
  <div class="page-stack">
    <div class="page-intro">
      <div>
        <h1>订单与成交</h1>
        <p>订单按客户端订单号聚合生命周期，成交保持逐笔记录；筛选和选中订单保存在 URL。</p>
      </div>
      <div class="ledger-badge"><ReceiptText :size="15" aria-hidden="true" />Execution ledger</div>
    </div>

    <section class="surface records-card">
      <div class="records-toolbar">
        <div>
          <p class="eyebrow">Paper execution</p>
          <h2>{{ activeTab === 'orders' ? '订单生命周期' : '成交明细' }}</h2>
        </div>
        <SegmentedTabs v-model="activeTab" :tabs="tabs" label="执行记录类型" />
      </div>

      <form class="filter-bar" @submit.prevent="applyInstrument">
        <label v-if="activeTab === 'orders'">
          <span>状态</span>
          <select :value="statusFilter" @change="changeStatus">
            <option value="">全部状态</option>
            <option value="NEW">待处理</option>
            <option value="SUBMITTED">已提交</option>
            <option value="ACCEPTED">已接受</option>
            <option value="PARTIALLY_FILLED">部分成交</option>
            <option value="FILLED">已成交</option>
            <option value="CANCELLED">已取消</option>
            <option value="REJECTED">已拒绝</option>
          </select>
        </label>
        <label class="instrument-filter"
          ><span>标的 ID</span
          ><input
            v-model="instrumentDraft"
            type="search"
            maxlength="128"
            placeholder="例如 AAPL.US"
        /></label>
        <button class="apply-filter" type="submit">应用标的</button>
        <button
          v-if="statusFilter || instrumentFilter"
          class="clear-filter"
          type="button"
          @click="clearFilters"
        >
          清除筛选
        </button>
      </form>

      <template v-if="activeTab === 'orders'">
        <DataState v-if="orders.isPending.value" state="loading" />
        <DataState
          v-else-if="orders.isError.value"
          state="error"
          :detail="orders.error.value?.message"
          retry-label="重新读取订单"
          @retry="retryOrders"
        />
        <DataState
          v-else-if="orders.data.value?.items.length === 0"
          state="empty"
          title="没有匹配的订单"
          detail="当前筛选条件下没有订单生命周期。"
        />
        <template v-else-if="orders.data.value">
          <DataTable caption="订单生命周期" min-width="1080px">
            <thead>
              <tr>
                <th>订单 / 事件</th>
                <th>标的</th>
                <th>方向</th>
                <th class="align-right">订单数量</th>
                <th class="align-right">已成交</th>
                <th>状态</th>
                <th>最近更新</th>
                <th class="align-right">事件 / 成交</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="order in orders.data.value.items" :key="order.client_order_id">
                <td>
                  <button
                    class="row-trigger"
                    type="button"
                    @click="openOrder(order.client_order_id)"
                  >
                    <span class="cell-primary mono" :title="order.client_order_id">{{
                      order.client_order_id
                    }}</span
                    ><span class="cell-subtle mono" :title="order.event_id">{{
                      order.event_id
                    }}</span>
                  </button>
                </td>
                <td class="cell-primary">{{ order.instrument_id }}</td>
                <td>
                  <StatusPill
                    :status="order.direction"
                    :label="formatDirection(order.direction)"
                    :dot="false"
                  />
                </td>
                <td class="align-right cell-primary tabular">
                  {{ formatQuantity(order.quantity) }}
                </td>
                <td class="align-right tabular">{{ formatQuantity(order.filled_quantity) }}</td>
                <td><StatusPill :status="order.status" :label="formatStatus(order.status)" /></td>
                <td>
                  <div class="cell-stack">
                    <span class="cell-primary tabular">{{
                      formatDateTime(order.latest_event_at_utc)
                    }}</span
                    ><span class="cell-subtle"
                      >创建 {{ formatDateTime(order.first_event_at_utc) }}</span
                    >
                  </div>
                </td>
                <td class="align-right tabular">
                  <strong class="cell-primary">{{ order.event_count }}</strong
                  ><span class="count-separator"> / </span>{{ order.fill_count }}
                </td>
              </tr>
            </tbody>
          </DataTable>
          <PaginationControls
            :offset="orders.data.value.offset"
            :limit="orders.data.value.limit"
            :item-count="orders.data.value.items.length"
            :has-more="orders.data.value.has_more"
            @change="changeOffset"
          />
        </template>
      </template>

      <template v-else>
        <DataState v-if="fills.isPending.value" state="loading" />
        <DataState
          v-else-if="fills.isError.value"
          state="error"
          :detail="fills.error.value?.message"
          retry-label="重新读取成交"
          @retry="retryFills"
        />
        <DataState
          v-else-if="fills.data.value?.items.length === 0"
          state="empty"
          title="没有匹配的成交"
          detail="当前筛选条件下没有逐笔成交。"
        />
        <template v-else-if="fills.data.value">
          <DataTable caption="逐笔成交记录" min-width="1100px">
            <thead>
              <tr>
                <th>成交时间</th>
                <th>标的</th>
                <th>方向</th>
                <th class="align-right">数量</th>
                <th class="align-right">成交价</th>
                <th class="align-right">佣金</th>
                <th>策略</th>
                <th>成交 / 订单标识</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="fill in fills.data.value.items" :key="fill.trade_id">
                <td class="cell-primary tabular">{{ formatDateTime(fill.timestamp_utc) }}</td>
                <td class="cell-primary">{{ fill.instrument_id }}</td>
                <td>
                  <StatusPill
                    :status="fill.direction"
                    :label="formatDirection(fill.direction)"
                    :dot="false"
                  />
                </td>
                <td class="align-right cell-primary tabular">
                  {{ formatQuantity(fill.quantity) }}
                </td>
                <td class="align-right cell-primary tabular">
                  {{ formatCurrency(fill.price, 'USD') }}
                </td>
                <td class="align-right tabular">{{ formatCurrency(fill.commission, 'USD') }}</td>
                <td>{{ formatStrategyName(fill.strategy_name) }}</td>
                <td>
                  <div class="cell-stack id-cell">
                    <span class="cell-primary mono" :title="fill.trade_id">{{ fill.trade_id }}</span
                    ><span class="cell-subtle mono" :title="fill.client_order_id">{{
                      fill.client_order_id
                    }}</span>
                  </div>
                </td>
              </tr>
            </tbody>
          </DataTable>
          <PaginationControls
            :offset="fills.data.value.offset"
            :limit="fills.data.value.limit"
            :item-count="fills.data.value.items.length"
            :has-more="fills.data.value.has_more"
            @change="changeOffset"
          />
        </template>
      </template>
    </section>

    <aside class="execution-boundary surface">
      <div class="boundary-icon" aria-hidden="true">
        <Landmark :size="19" :stroke-width="1.7" />
      </div>
      <div>
        <strong>这里不是下单终端</strong>
        <p>Web 只读取审计结果；订单仍只能由 ExecutionGatewayStrategy 提交至 NautilusTrader。</p>
      </div>
    </aside>

    <SideDrawer
      :open="Boolean(selectedOrderId)"
      title="订单生命周期"
      :description="selectedOrderId"
      @close="closeOrder"
    >
      <DataState v-if="orderDetail.isPending.value" state="loading" />
      <DataState
        v-else-if="orderDetail.isError.value"
        state="error"
        :detail="orderDetail.error.value?.message"
      />
      <template v-else-if="orderDetail.data.value">
        <div class="order-heading">
          <div>
            <span>{{ orderDetail.data.value.summary.instrument_id }}</span
            ><strong
              >{{ formatDirection(orderDetail.data.value.summary.direction) }}
              {{ formatQuantity(orderDetail.data.value.summary.quantity) }}</strong
            >
          </div>
          <StatusPill
            :status="orderDetail.data.value.summary.status"
            :label="formatStatus(orderDetail.data.value.summary.status)"
          />
        </div>

        <dl class="order-metrics">
          <div>
            <dt>已成交数量</dt>
            <dd>{{ formatQuantity(orderDetail.data.value.summary.filled_quantity) }}</dd>
          </div>
          <div>
            <dt>状态事件</dt>
            <dd>{{ orderDetail.data.value.summary.event_count }}</dd>
          </div>
          <div>
            <dt>逐笔成交</dt>
            <dd>{{ orderDetail.data.value.summary.fill_count }}</dd>
          </div>
          <div>
            <dt>信号事件</dt>
            <dd class="mono">{{ orderDetail.data.value.summary.event_id }}</dd>
          </div>
        </dl>

        <section class="drawer-section">
          <div class="drawer-section-title">
            <h3>订单事件</h3>
            <p>所有状态变更按 UTC 时间排列。</p>
          </div>
          <ol v-if="orderDetail.data.value.events.length" class="event-list">
            <li
              v-for="event in orderDetail.data.value.events"
              :key="`${event.timestamp_utc}:${event.status}`"
            >
              <span class="event-mark" aria-hidden="true"></span>
              <div>
                <div>
                  <StatusPill :status="event.status" :label="formatStatus(event.status)" /><strong
                    >{{ formatQuantity(event.quantity) }} 股</strong
                  >
                </div>
                <p>{{ event.reason }}</p>
                <small>{{ formatDateTime(event.timestamp_utc) }}</small>
              </div>
            </li>
          </ol>
          <DataState
            v-else
            state="empty"
            title="没有订单事件"
            detail="订单摘要存在，但没有状态事件。"
          />
        </section>

        <section class="drawer-section">
          <div class="drawer-section-title">
            <h3>逐笔成交</h3>
            <p>成交记录保持原始 trade ID 和佣金。</p>
          </div>
          <div v-if="orderDetail.data.value.fills.length" class="drawer-fills">
            <article v-for="fill in orderDetail.data.value.fills" :key="fill.trade_id">
              <div>
                <span class="mono">{{ fill.trade_id }}</span
                ><strong>{{ formatCurrency(fill.price, 'USD') }}</strong>
              </div>
              <p>
                {{ formatDirection(fill.direction) }} {{ formatQuantity(fill.quantity) }} 股 · 佣金
                {{ formatCurrency(fill.commission, 'USD') }}
              </p>
              <small>{{ formatDateTime(fill.timestamp_utc) }}</small>
            </article>
          </div>
          <DataState
            v-else
            state="empty"
            title="尚无成交"
            detail="订单存在，但还没有逐笔成交记录。"
          />
        </section>
      </template>
    </SideDrawer>
  </div>
</template>

<style scoped>
.ledger-badge {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 8px 11px;
  color: var(--color-info);
  font-size: 0.68rem;
  font-weight: 670;
  letter-spacing: 0.02em;
  white-space: nowrap;
  background: var(--color-info-bg);
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
  max-width: 240px;
  padding: 0;
  text-align: left;
  cursor: pointer;
  background: transparent;
}
.row-trigger:hover .cell-primary {
  color: var(--color-brand-blue);
}
.row-trigger span,
.id-cell span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.count-separator {
  color: var(--color-line-strong);
}
.id-cell {
  max-width: 240px;
}
.execution-boundary {
  display: flex;
  align-items: flex-start;
  gap: 14px;
  padding: 18px 20px;
}
.boundary-icon {
  display: grid;
  flex: 0 0 auto;
  width: 38px;
  height: 38px;
  color: var(--color-positive);
  background: var(--color-positive-bg);
  border-radius: 12px;
  place-items: center;
}
.execution-boundary strong {
  font-size: 0.8rem;
}
.execution-boundary p {
  margin: 5px 0 0;
  color: var(--color-text-soft);
  font-size: 0.73rem;
  line-height: 1.5;
}

.order-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 16px;
  background: var(--color-surface-strong);
  border-radius: var(--radius-md);
}
.order-heading div {
  display: grid;
  gap: 6px;
  color: #fff;
}
.order-heading span {
  color: #9da0a8;
  font-size: 0.68rem;
}
.order-heading strong {
  font-size: 1.1rem;
}
.order-metrics {
  display: grid;
  gap: 1px;
  margin: 16px 0 0;
  overflow: hidden;
  background: var(--color-line);
  border: 1px solid var(--color-line);
  border-radius: 13px;
  grid-template-columns: repeat(2, minmax(0, 1fr));
}
.order-metrics div {
  display: grid;
  gap: 5px;
  min-width: 0;
  padding: 12px;
  background: var(--color-surface-soft);
}
.order-metrics dt {
  color: var(--color-text-faint);
  font-size: 0.61rem;
  text-transform: uppercase;
}
.order-metrics dd {
  margin: 0;
  overflow: hidden;
  font-size: 0.7rem;
  font-weight: 650;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.drawer-section {
  padding: 24px 0;
  border-bottom: 1px solid var(--color-line);
}
.drawer-section:last-child {
  border-bottom: 0;
}
.drawer-section-title {
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
}
.event-list {
  display: grid;
  padding: 0;
  margin: 0;
  list-style: none;
}
.event-list li {
  position: relative;
  display: grid;
  gap: 12px;
  padding: 0 0 22px 22px;
  grid-template-columns: 10px minmax(0, 1fr);
}
.event-list li::before {
  position: absolute;
  top: 9px;
  bottom: -9px;
  left: 4px;
  width: 1px;
  content: '';
  background: var(--color-line-strong);
}
.event-list li:last-child::before {
  display: none;
}
.event-mark {
  z-index: 1;
  width: 9px;
  height: 9px;
  margin-top: 6px;
  background: var(--color-brand-blue);
  border: 2px solid #fff;
  border-radius: 50%;
  box-shadow: 0 0 0 2px #cbd9ff;
}
.event-list li > div > div {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}
.event-list strong {
  font-size: 0.7rem;
}
.event-list p {
  margin: 6px 0;
  color: var(--color-text-soft);
  font-size: 0.69rem;
}
.event-list small,
.drawer-fills small {
  color: var(--color-text-faint);
  font-size: 0.62rem;
}
.drawer-fills {
  display: grid;
  gap: 9px;
}
.drawer-fills article {
  padding: 13px;
  background: var(--color-surface-soft);
  border: 1px solid var(--color-line);
  border-radius: 12px;
}
.drawer-fills article > div {
  display: flex;
  justify-content: space-between;
  gap: 12px;
}
.drawer-fills span,
.drawer-fills strong {
  font-size: 0.7rem;
}
.drawer-fills p {
  margin: 8px 0 5px;
  color: var(--color-text-soft);
  font-size: 0.68rem;
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
  .order-metrics {
    grid-template-columns: 1fr;
  }
}
</style>

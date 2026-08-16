<script setup lang="ts">
import { Clock3, Landmark } from '@lucide/vue'
import { useQuery } from '@tanstack/vue-query'
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { portfolioHistoryQuery, portfolioQuery } from '../api/queries'
import type { PortfolioHistoryQuery } from '../api/types'
import DataChart from '../components/DataChart.vue'
import DataState from '../components/DataState.vue'
import DataTable from '../components/DataTable.vue'
import MetricCard from '../components/MetricCard.vue'
import PaginationControls from '../components/PaginationControls.vue'
import SegmentedTabs from '../components/SegmentedTabs.vue'
import StatusPill from '../components/StatusPill.vue'
import {
  formatAge,
  formatCurrency,
  formatDateTime,
  formatDirection,
  formatPercent,
  formatQuantity,
  formatSourceState,
} from '../utils/format'
import { CHART_WINDOW_SIZE, PAGE_SIZE, pageOffset, readPage, withPage } from '../utils/pagination'

const route = useRoute()
const router = useRouter()
const historyPage = computed(() => readPage(route.query.page))
const historyParams = computed<PortfolioHistoryQuery>(() => ({
  offset: pageOffset(historyPage.value),
  limit: PAGE_SIZE,
}))
const {
  data: portfolioData,
  isPending: portfolioPending,
  isError: portfolioError,
  error: portfolioReadError,
  refetch: refetchPortfolio,
} = useQuery(portfolioQuery())
const {
  data: historyData,
  isPending: historyPending,
  isError: historyError,
  error: historyReadError,
  refetch: refetchHistory,
} = useQuery(computed(() => portfolioHistoryQuery(historyParams.value)))
const {
  data: chartHistoryData,
  isPending: chartHistoryPending,
  isError: chartHistoryError,
  error: chartHistoryReadError,
} = useQuery(portfolioHistoryQuery({ offset: 0, limit: CHART_WINDOW_SIZE }))

const historyChartPoints = computed(() =>
  [...(chartHistoryData.value?.items ?? [])].reverse().map((point) => ({
    label: point.timestamp_utc.slice(0, 10),
    value: point.net_liquidation,
  })),
)

const activeTab = computed({
  get: () => (route.query.tab === 'history' ? 'history' : 'positions'),
  set: (value: string) => {
    const query = { ...route.query }
    if (value === 'history') {
      query.tab = 'history'
    } else {
      delete query.tab
      delete query.page
    }
    void router.replace({ query })
  },
})

const tabs = computed(() => [
  { value: 'positions', label: '当前持仓', count: portfolioData.value?.positions.length ?? 0 },
  { value: 'history', label: '资金快照', count: historyData.value?.items.length ?? 0 },
])

async function retryPortfolio(): Promise<void> {
  await refetchPortfolio()
}

async function retryHistory(): Promise<void> {
  await refetchHistory()
}

function changeHistoryOffset(offset: number): void {
  void router.replace({ query: withPage(route.query, Math.floor(offset / PAGE_SIZE) + 1) })
}
</script>

<template>
  <div class="page-stack">
    <div class="page-intro">
      <div>
        <h1>账户与持仓</h1>
        <p>账户净值来自业务快照；持仓估值使用明确标注的 EOD 参考价，不代表盘中实时市值。</p>
      </div>
      <StatusPill
        v-if="portfolioData"
        :status="portfolioData.is_stale ? 'new' : portfolioData.source_state"
        :label="
          portfolioData.is_stale ? '账户快照已过期' : formatSourceState(portfolioData.source_state)
        "
      />
    </div>

    <DataState v-if="portfolioPending" state="loading" />
    <DataState
      v-else-if="portfolioError"
      state="error"
      :detail="portfolioReadError?.message"
      retry-label="重新读取账户"
      @retry="retryPortfolio"
    />

    <template v-else-if="portfolioData">
      <section class="account-summary surface">
        <div class="account-primary">
          <div class="account-heading">
            <div>
              <p class="eyebrow">Net liquidation</p>
              <span>{{ portfolioData.account_id || '账户快照不可用' }}</span>
            </div>
            <div class="account-icon" aria-hidden="true">
              <Landmark :size="22" :stroke-width="1.7" />
            </div>
          </div>
          <strong class="account-value tabular">
            {{ formatCurrency(portfolioData.net_liquidation, portfolioData.currency) }}
          </strong>
          <div class="snapshot-meta">
            <span
              ><Clock3 :size="14" aria-hidden="true" />{{
                formatAge(portfolioData.age_seconds)
              }}</span
            >
            <span>{{ formatDateTime(portfolioData.snapshot_at_utc) }}</span>
          </div>
        </div>
        <div class="account-rule" aria-hidden="true"></div>
        <div class="account-secondary">
          <div>
            <span>账户币种</span>
            <strong>{{ portfolioData.currency || '—' }}</strong>
          </div>
          <div>
            <span>持仓数量</span>
            <strong>{{ portfolioData.positions.length }}</strong>
          </div>
          <div>
            <span>价格语义</span>
            <strong>EOD reference</strong>
          </div>
        </div>
      </section>

      <DataState
        v-if="portfolioData.source_state !== 'available'"
        :state="portfolioData.source_state"
        title="完整账户快照不可用"
        detail="前端不会根据订单、成交或持仓自行反推账户资金。"
      />

      <section class="metric-grid" aria-label="现金摘要">
        <MetricCard
          label="可用现金"
          :value="formatCurrency(portfolioData.free_cash, portfolioData.currency)"
          helper="账户快照中的 free cash"
          accent
        />
        <MetricCard
          label="锁定现金"
          :value="formatCurrency(portfolioData.locked_cash, portfolioData.currency)"
          helper="账户快照中的 locked cash"
        />
        <MetricCard
          label="现金合计"
          :value="
            formatCurrency(
              portfolioData.free_cash === null || portfolioData.locked_cash === null
                ? null
                : portfolioData.free_cash + portfolioData.locked_cash,
              portfolioData.currency,
            )
          "
          helper="仅为两项现金字段之和"
        />
      </section>

      <section class="surface records-card">
        <div class="records-toolbar">
          <div>
            <p class="eyebrow">Account ledger</p>
            <h2>{{ activeTab === 'positions' ? '持仓明细' : '最近资金快照' }}</h2>
          </div>
          <SegmentedTabs v-model="activeTab" :tabs="tabs" label="账户记录类型" />
        </div>

        <template v-if="activeTab === 'positions'">
          <DataState
            v-if="portfolioData.positions.length === 0"
            state="empty"
            title="当前没有持仓"
            detail="账户快照有效，但没有开放持仓。"
          />
          <DataTable v-else caption="当前持仓明细" min-width="980px">
            <thead>
              <tr>
                <th>标的</th>
                <th>方向 / 数量</th>
                <th class="align-right">开仓均价</th>
                <th class="align-right">EOD 参考价</th>
                <th class="align-right">估算市值</th>
                <th class="align-right">估算权重</th>
                <th class="align-right">已实现盈亏</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="position in portfolioData.positions" :key="position.canonical_id">
                <td>
                  <div class="cell-stack">
                    <span class="cell-primary">{{ position.symbol }}</span>
                    <span class="cell-subtle mono">{{ position.canonical_id }}</span>
                  </div>
                </td>
                <td>
                  <div class="direction-cell">
                    <StatusPill
                      :status="position.side"
                      :label="formatDirection(position.side)"
                      :dot="false"
                    />
                    <span class="tabular">{{ formatQuantity(position.signed_quantity) }}</span>
                  </div>
                </td>
                <td class="align-right tabular">
                  {{ formatCurrency(position.avg_open_price, portfolioData.currency) }}
                </td>
                <td class="align-right">
                  <div class="cell-stack price-cell">
                    <span class="cell-primary tabular">
                      {{ formatCurrency(position.reference_price, portfolioData.currency) }}
                    </span>
                    <span class="cell-subtle">{{
                      formatDateTime(position.reference_price_at_utc)
                    }}</span>
                  </div>
                </td>
                <td class="align-right cell-primary tabular">
                  {{ formatCurrency(position.estimated_market_value, portfolioData.currency) }}
                </td>
                <td class="align-right tabular">{{ formatPercent(position.estimated_weight) }}</td>
                <td class="align-right tabular">
                  {{ formatCurrency(position.realized_pnl, portfolioData.currency) }}
                </td>
              </tr>
            </tbody>
          </DataTable>
          <p v-if="portfolioData.positions.length" class="table-note">
            参考价类型由后端保存；这些估值只用于日线级观察，不是 IBKR 实时行情。
          </p>
        </template>

        <template v-else>
          <DataState v-if="historyPending" state="loading" />
          <DataState
            v-else-if="historyError"
            state="error"
            :detail="historyReadError?.message"
            retry-label="重新读取快照"
            @retry="retryHistory"
          />
          <DataState
            v-else-if="historyData?.items.length === 0"
            state="empty"
            title="暂无资金历史"
            detail="当前业务库里还没有历史账户快照。"
          />
          <template v-else-if="historyData">
            <div class="history-chart">
              <div class="chart-heading">
                <div>
                  <p class="eyebrow">Account history</p>
                  <h3>净清算价值</h3>
                </div>
                <span>最近 {{ chartHistoryData?.items.length ?? 0 }} 个快照</span>
              </div>
              <DataState v-if="chartHistoryPending" state="loading" />
              <DataState
                v-else-if="chartHistoryError"
                state="error"
                :detail="chartHistoryReadError?.message"
              />
              <DataState
                v-else-if="historyChartPoints.length === 0"
                state="empty"
                title="没有可绘制的净值历史"
                detail="账户历史表仍可独立分页检查。"
              />
              <DataChart
                v-else
                title="账户净清算价值历史"
                kind="line"
                value-kind="currency"
                :points="historyChartPoints"
                :height="300"
              />
            </div>
            <DataTable caption="最近资金快照" min-width="760px">
              <thead>
                <tr>
                  <th>快照时间</th>
                  <th>账户</th>
                  <th class="align-right">净清算价值</th>
                  <th class="align-right">可用现金</th>
                  <th class="align-right">锁定现金</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="point in historyData.items" :key="point.timestamp_utc">
                  <td class="cell-primary tabular">{{ formatDateTime(point.timestamp_utc) }}</td>
                  <td class="mono">{{ point.account_id }}</td>
                  <td class="align-right cell-primary tabular">
                    {{ formatCurrency(point.net_liquidation, point.currency) }}
                  </td>
                  <td class="align-right tabular">
                    {{ formatCurrency(point.free_cash, point.currency) }}
                  </td>
                  <td class="align-right tabular">
                    {{ formatCurrency(point.locked_cash, point.currency) }}
                  </td>
                </tr>
              </tbody>
            </DataTable>
            <PaginationControls
              :offset="historyData.offset"
              :limit="historyData.limit"
              :item-count="historyData.items.length"
              :has-more="historyData.has_more"
              @change="changeHistoryOffset"
            />
          </template>
        </template>
      </section>
    </template>
  </div>
</template>

<style scoped>
.account-summary {
  display: grid;
  min-height: 220px;
  padding: 32px;
  grid-template-columns: minmax(0, 1.4fr) 1px minmax(240px, 0.6fr);
}

.account-primary {
  display: grid;
  align-content: space-between;
  padding-right: 34px;
}

.account-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
}

.account-heading span {
  color: var(--color-text-soft);
  font-size: 0.75rem;
}

.account-icon {
  display: grid;
  width: 42px;
  height: 42px;
  color: var(--color-brand-violet);
  background: #f1edff;
  border-radius: 14px;
  place-items: center;
}

.account-value {
  margin: 24px 0;
  font-size: clamp(2.45rem, 5.2vw, 4.5rem);
  font-weight: 610;
  line-height: 1;
  letter-spacing: -0.065em;
}

.snapshot-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 9px 20px;
  color: var(--color-text-faint);
  font-size: 0.69rem;
}

.snapshot-meta span {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.account-rule {
  background: var(--color-line);
}

.account-secondary {
  display: grid;
  align-content: center;
  gap: 21px;
  padding-left: 34px;
}

.account-secondary div {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.account-secondary span {
  color: var(--color-text-soft);
  font-size: 0.72rem;
}

.account-secondary strong {
  font-size: 0.78rem;
}

.metric-grid {
  display: grid;
  gap: 14px;
  grid-template-columns: repeat(3, minmax(0, 1fr));
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

.direction-cell {
  display: flex;
  align-items: center;
  gap: 10px;
}

.price-cell {
  justify-items: end;
}

.history-chart {
  padding: 22px 24px 26px;
  border-top: 1px solid var(--color-line);
}

.chart-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 14px;
}

.chart-heading h3 {
  margin: 0;
  font-size: 0.98rem;
  letter-spacing: -0.02em;
}

.chart-heading > span {
  color: var(--color-text-faint);
  font-size: 0.68rem;
}

.history-chart :deep(.data-state) {
  min-height: 110px;
}

@media (max-width: 860px) {
  .account-summary {
    grid-template-columns: 1fr;
  }

  .account-primary {
    padding-right: 0;
  }

  .account-rule {
    width: 100%;
    height: 1px;
    margin: 26px 0;
  }

  .account-secondary {
    padding-left: 0;
  }

  .metric-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 660px) {
  .account-summary {
    padding: 24px;
  }

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
}
</style>

<script setup lang="ts">
import { AlertTriangle, Database, RadioTower } from '@lucide/vue'
import { useQuery } from '@tanstack/vue-query'
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { catalogQuery, dataQualityQuery, systemQuery } from '../api/queries'
import DataState from '../components/DataState.vue'
import DataTable from '../components/DataTable.vue'
import MetricCard from '../components/MetricCard.vue'
import SegmentedTabs from '../components/SegmentedTabs.vue'
import StatusPill from '../components/StatusPill.vue'
import { formatDateTime, formatNumber, formatSourceState, formatStatus } from '../utils/format'

const route = useRoute()
const router = useRouter()
const catalog = useQuery(catalogQuery())
const quality = useQuery(dataQualityQuery())
const system = useQuery(systemQuery())

const activeTab = computed({
  get: () => {
    const tab = route.query.tab
    return tab === 'quality' || tab === 'sources' ? tab : 'catalog'
  },
  set: (value: string) => {
    const query = { ...route.query }
    if (value === 'catalog') {
      delete query.tab
    } else {
      query.tab = value
    }
    void router.replace({ query })
  },
})

const tabs = computed(() => [
  { value: 'catalog', label: 'Catalog 覆盖', count: catalog.data.value?.coverage.length ?? 0 },
  { value: 'quality', label: '数据质量', count: quality.data.value?.issues.length ?? 0 },
  { value: 'sources', label: '系统来源', count: system.data.value?.sources.length ?? 0 },
])

const sortedCoverage = computed(() =>
  [...(catalog.data.value?.coverage ?? [])].sort((left, right) =>
    `${left.symbol}:${left.price_kind}`.localeCompare(`${right.symbol}:${right.price_kind}`),
  ),
)

async function retryCatalog(): Promise<void> {
  await catalog.refetch()
}

async function retryQuality(): Promise<void> {
  await quality.refetch()
}

async function retrySystem(): Promise<void> {
  await system.refetch()
}
</script>

<template>
  <div class="page-stack">
    <div class="page-intro">
      <div>
        <h1>数据与系统</h1>
        <p>
          展示 Web
          可以直接证明的数据覆盖、最近质量检查和来源状态；未被观测的运行时不会被标记为离线。
        </p>
      </div>
      <div class="source-summary" aria-label="数据源状态摘要">
        <StatusPill
          v-if="catalog.data.value"
          :status="catalog.data.value.source_state"
          :label="`Catalog · ${formatSourceState(catalog.data.value.source_state)}`"
        />
        <StatusPill
          v-if="quality.data.value"
          :status="quality.data.value.source_state"
          :label="`质量 · ${formatSourceState(quality.data.value.source_state)}`"
        />
      </div>
    </div>

    <section class="surface system-card">
      <div class="system-toolbar">
        <div>
          <p class="eyebrow">Read-only observability</p>
          <h2>
            {{
              activeTab === 'catalog'
                ? '历史数据覆盖'
                : activeTab === 'quality'
                  ? '最近质量检查'
                  : '可证明的系统状态'
            }}
          </h2>
        </div>
        <SegmentedTabs v-model="activeTab" :tabs="tabs" label="数据与系统视图" />
      </div>

      <template v-if="activeTab === 'catalog'">
        <DataState v-if="catalog.isPending.value" state="loading" />
        <DataState
          v-else-if="catalog.isError.value"
          state="error"
          :detail="catalog.error.value?.message"
          retry-label="重新读取 Catalog"
          @retry="retryCatalog"
        />
        <DataState
          v-else-if="catalog.data.value?.source_state !== 'available'"
          :state="catalog.data.value?.source_state || 'empty'"
          title="Catalog 覆盖不可用"
          detail="配置股票池仍会显示缺失边界，但不会被当作已有历史数据。"
        />
        <template v-if="catalog.data.value && sortedCoverage.length">
          <div class="catalog-context">
            <div class="context-icon"><Database :size="19" aria-hidden="true" /></div>
            <div>
              <span>历史数据提供方</span>
              <strong>{{ catalog.data.value.provider }}</strong>
            </div>
            <div>
              <span>观测时间</span>
              <strong>{{ formatDateTime(catalog.data.value.observed_at_utc) }}</strong>
            </div>
            <p>signal 与 execution 是同一标的的两种明确价格语义，不合并为一条模糊日线。</p>
          </div>
          <DataTable caption="配置股票池历史数据覆盖" min-width="1040px">
            <thead>
              <tr>
                <th>标的</th>
                <th>用途</th>
                <th>状态</th>
                <th>首条 Bar</th>
                <th>末条 Bar</th>
                <th>Bar Type</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in sortedCoverage" :key="`${row.instrument_id}:${row.price_kind}`">
                <td>
                  <div class="cell-stack">
                    <span class="cell-primary">{{ row.symbol }}</span>
                    <span class="cell-subtle mono">{{ row.instrument_id }}</span>
                  </div>
                </td>
                <td>
                  <span class="price-kind" :class="`price-kind--${row.price_kind}`">
                    {{ row.price_kind === 'signal' ? '信号计算' : '模拟成交' }}
                  </span>
                </td>
                <td><StatusPill :status="row.state" :label="formatSourceState(row.state)" /></td>
                <td class="tabular">{{ formatDateTime(row.first_at_utc) }}</td>
                <td class="tabular">{{ formatDateTime(row.last_at_utc) }}</td>
                <td class="mono bar-type">{{ row.bar_type }}</td>
              </tr>
            </tbody>
          </DataTable>
        </template>
      </template>

      <template v-else-if="activeTab === 'quality'">
        <DataState v-if="quality.isPending.value" state="loading" />
        <DataState
          v-else-if="quality.isError.value"
          state="error"
          :detail="quality.error.value?.message"
          retry-label="重新读取质量报告"
          @retry="retryQuality"
        />
        <DataState
          v-else-if="quality.data.value?.source_state !== 'available'"
          :state="quality.data.value?.source_state || 'empty'"
          title="数据质量报告不可用"
          detail="页面不会仅根据 Catalog 存在推断最近同步已经通过质量检查。"
        />
        <template v-else-if="quality.data.value">
          <section class="metric-grid quality-metrics" aria-label="数据质量摘要">
            <MetricCard
              label="读取 Bar"
              :value="formatNumber(quality.data.value.bars_fetched, 0)"
              :helper="quality.data.value.mode || '—'"
              accent
            />
            <MetricCard
              label="新增 Bar"
              :value="formatNumber(quality.data.value.bars_written, 0)"
              helper="通过去重后写入"
            />
            <MetricCard
              label="公司行动"
              :value="formatNumber(quality.data.value.corporate_actions_written, 0)"
              helper="本次写入条数"
            />
            <MetricCard
              label="警告 / 错误"
              :value="`${String(quality.data.value.warning_count)} / ${String(quality.data.value.error_count)}`"
              :helper="formatDateTime(quality.data.value.generated_at_utc)"
            />
          </section>

          <div v-if="quality.data.value.issues.length" class="quality-block">
            <div class="section-header compact-header">
              <div>
                <p class="eyebrow">Issues</p>
                <h2>质量问题</h2>
              </div>
              <AlertTriangle :size="19" aria-hidden="true" />
            </div>
            <DataTable caption="最近数据质量问题" min-width="900px">
              <thead>
                <tr>
                  <th>严重度</th>
                  <th>标的</th>
                  <th>代码</th>
                  <th>时间</th>
                  <th>说明</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="(issue, index) in quality.data.value.issues"
                  :key="`${issue.code}:${issue.instrument_id}:${index}`"
                >
                  <td>
                    <StatusPill :status="issue.severity" :label="formatStatus(issue.severity)" />
                  </td>
                  <td class="cell-primary">{{ issue.instrument_id || '全局' }}</td>
                  <td class="mono">{{ issue.code }}</td>
                  <td class="tabular">{{ formatDateTime(issue.timestamp_utc) }}</td>
                  <td>{{ issue.message }}</td>
                </tr>
              </tbody>
            </DataTable>
          </div>
          <DataState
            v-else
            class="quality-empty"
            state="empty"
            title="没有质量问题"
            detail="最近一份报告没有记录 warning 或 error。"
          />

          <div v-if="quality.data.value.instruments.length" class="quality-block">
            <div class="section-header compact-header">
              <div>
                <p class="eyebrow">Per instrument</p>
                <h2>逐标的摘要</h2>
              </div>
            </div>
            <DataTable caption="逐标的数据质量摘要" min-width="760px">
              <thead>
                <tr>
                  <th>标的</th>
                  <th class="align-right">Bar 数</th>
                  <th>首条</th>
                  <th>末条</th>
                  <th class="align-right">问题数</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="item in quality.data.value.instruments" :key="item.instrument_id">
                  <td class="cell-primary">{{ item.instrument_id }}</td>
                  <td class="align-right tabular">{{ formatNumber(item.bar_count, 0) }}</td>
                  <td class="tabular">{{ formatDateTime(item.first_at_utc) }}</td>
                  <td class="tabular">{{ formatDateTime(item.last_at_utc) }}</td>
                  <td class="align-right tabular">{{ item.issue_count }}</td>
                </tr>
              </tbody>
            </DataTable>
          </div>
        </template>
      </template>

      <template v-else>
        <DataState v-if="system.isPending.value" state="loading" />
        <DataState
          v-else-if="system.isError.value"
          state="error"
          :detail="system.error.value?.message"
          retry-label="重新读取系统状态"
          @retry="retrySystem"
        />
        <DataState
          v-else-if="system.data.value?.sources.length === 0"
          state="empty"
          title="没有系统来源"
          detail="Web API 没有返回可观测来源。"
        />
        <template v-else-if="system.data.value">
          <div class="observation-note">
            <RadioTower :size="18" aria-hidden="true" />
            <p>
              状态观测于 {{ formatDateTime(system.data.value.observed_at_utc) }}。unobserved
              表示没有证据，不等于服务离线。
            </p>
          </div>
          <div class="source-grid">
            <article
              v-for="source in system.data.value.sources"
              :key="source.name"
              class="source-card"
            >
              <div>
                <span>{{ source.name.replaceAll('_', ' ') }}</span>
                <StatusPill :status="source.state" :label="formatSourceState(source.state)" />
              </div>
              <p>{{ source.detail }}</p>
              <small>最近事件 · {{ formatDateTime(source.last_event_at_utc) }}</small>
            </article>
          </div>
        </template>
      </template>
    </section>
  </div>
</template>

<style scoped>
.source-summary {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
}

.system-card {
  padding-top: 24px;
}

.system-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  padding: 0 24px 20px;
}

.system-toolbar h2,
.compact-header h2 {
  margin: 0;
  font-size: 1.08rem;
  letter-spacing: -0.02em;
}

.system-card > :deep(.data-state) {
  margin: 0 24px 24px;
}

.catalog-context {
  display: grid;
  align-items: center;
  gap: 14px 22px;
  padding: 18px 24px;
  background: var(--color-surface-soft);
  border-top: 1px solid var(--color-line);
  grid-template-columns: auto auto auto minmax(280px, 1fr);
}

.context-icon {
  display: grid;
  width: 38px;
  height: 38px;
  color: var(--color-brand-violet);
  background: #f1edff;
  border-radius: 12px;
  place-items: center;
}

.catalog-context div:not(.context-icon) {
  display: grid;
  gap: 4px;
}

.catalog-context span {
  color: var(--color-text-faint);
  font-size: 0.64rem;
  text-transform: uppercase;
}

.catalog-context strong,
.catalog-context p {
  margin: 0;
  font-size: 0.72rem;
}

.catalog-context p {
  justify-self: end;
  max-width: 390px;
  color: var(--color-text-soft);
  line-height: 1.5;
}

.price-kind {
  display: inline-flex;
  padding: 5px 8px;
  color: var(--color-text-soft);
  font-size: 0.67rem;
  font-weight: 650;
  background: var(--color-surface-soft);
  border-radius: 999px;
}

.price-kind--execution {
  color: var(--color-brand-violet);
  background: #f1edff;
}

.bar-type {
  max-width: 300px;
  overflow: hidden;
  font-size: 0.68rem;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.quality-metrics {
  padding: 0 24px 24px;
}

.quality-block {
  padding-top: 22px;
  border-top: 1px solid var(--color-line);
}

.compact-header {
  padding: 0 24px 18px;
  margin: 0;
}

.quality-empty {
  margin-top: 0 !important;
}

.observation-note {
  display: flex;
  align-items: center;
  gap: 11px;
  padding: 16px 20px;
  margin: 0 24px 20px;
  color: var(--color-info);
  background: var(--color-info-bg);
  border-radius: var(--radius-md);
}

.observation-note p {
  margin: 0;
  font-size: 0.73rem;
  line-height: 1.5;
}

.source-grid {
  display: grid;
  gap: 12px;
  padding: 0 24px 24px;
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.source-card {
  padding: 17px;
  background: var(--color-surface-soft);
  border: 1px solid var(--color-line);
  border-radius: var(--radius-md);
}

.source-card > div {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.source-card > div > span {
  font-size: 0.76rem;
  font-weight: 680;
  text-transform: capitalize;
}

.source-card p {
  min-height: 42px;
  margin: 14px 0;
  color: var(--color-text-soft);
  font-size: 0.72rem;
  line-height: 1.5;
}

.source-card small {
  color: var(--color-text-faint);
  font-size: 0.64rem;
}

@media (max-width: 1040px) {
  .source-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .catalog-context {
    grid-template-columns: auto 1fr 1fr;
  }

  .catalog-context p {
    justify-self: stretch;
    max-width: none;
    grid-column: 1 / -1;
  }
}

@media (max-width: 720px) {
  .system-toolbar {
    align-items: stretch;
    flex-direction: column;
  }

  .system-toolbar :deep(.segmented-tabs) {
    width: 100%;
    overflow-x: auto;
  }

  .catalog-context {
    grid-template-columns: auto 1fr;
  }

  .catalog-context div:nth-child(3) {
    grid-column: 1 / -1;
  }

  .source-grid {
    grid-template-columns: 1fr;
  }
}
</style>

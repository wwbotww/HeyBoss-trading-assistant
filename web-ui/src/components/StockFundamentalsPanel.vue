<script setup lang="ts">
import { ArrowUpRight } from '@lucide/vue'
import { computed } from 'vue'

import type { FundamentalMetric, MarketFundamentals, StockFundamentals } from '../api/types'
import { formatDateTime } from '../utils/format'
import DataState from './DataState.vue'
import FundamentalMetricValue from './FundamentalMetricValue.vue'
import StatusPill from './StatusPill.vue'

const props = defineProps<{
  data: MarketFundamentals | undefined
  loading: boolean
  error: string | undefined
}>()
const emit = defineEmits<{ retry: []; openStock: [instrumentId: string] }>()
const items = computed(() =>
  [...(props.data?.items ?? [])].sort((left, right) =>
    left.instrument_id.localeCompare(right.instrument_id),
  ),
)
const kindLabels: Readonly<Record<StockFundamentals['kind'], string>> = {
  operating: '一般企业',
  financial: '金融企业',
  reit: 'REIT',
  unknown: '分类未知',
}
const validityLabels = {
  complete: '适用字段完整',
  partial: '适用字段部分可用',
  unavailable: '适用字段不可用',
}
const sourceLabels = { recent: '来源近期更新', stale: '来源更新陈旧', unknown: '来源更新未知' }

function summaryMetrics(
  item: StockFundamentals,
  group: 'financial' | 'valuation',
): FundamentalMetric[] {
  const names: readonly FundamentalMetric['name'][] =
    item.kind === 'financial'
      ? group === 'financial'
        ? ['return_on_equity_ttm']
        : ['price_to_book']
      : group === 'financial'
        ? ['fcf_margin', 'net_debt_to_ebitda']
        : ['fcf_yield', 'forward_pe']
  return item.metrics.filter((metric) => names.includes(metric.name))
}
</script>

<template>
  <section class="fundamentals-panel" aria-labelledby="fundamentals-title">
    <header class="fundamentals-header">
      <div>
        <p class="eyebrow">Fundamentals snapshot</p>
        <h3 id="fundamentals-title">财务与估值</h3>
        <p>按标的展示整批已发布结果，不排名、不打分；不同公司类型使用不同适用指标。</p>
      </div>
      <StatusPill
        v-if="data?.source_state === 'available'"
        :status="data.validity"
        :label="validityLabels[data.validity]"
      />
    </header>
    <DataState v-if="loading" state="loading" title="正在读取基本面快照" />
    <DataState
      v-else-if="error"
      state="error"
      title="基本面快照读取失败"
      :detail="error"
      retry-label="重新读取基本面"
      @retry="emit('retry')"
    />
    <DataState
      v-else-if="data?.source_state !== 'available' || items.length === 0"
      :state="data?.source_state === 'missing' ? 'missing' : 'empty'"
      title="基本面快照尚未发布"
      detail="请先在离线任务中完成基本面同步；页面不会连接 EODHD、补数或触发交易。"
    />
    <template v-else-if="data">
      <dl class="fundamentals-meta">
        <div>
          <dt>基本面采集日 · UTC</dt>
          <dd>{{ data.as_of_date || '—' }}</dd>
        </div>
        <div>
          <dt>基本面计算时间</dt>
          <dd>{{ formatDateTime(data.calculated_at_utc) }}</dd>
        </div>
        <div>
          <dt>数据来源</dt>
          <dd>
            {{ data.source === 'eodhd_fundamentals' ? 'EODHD Fundamentals' : data.source || '—' }}
          </dd>
        </div>
        <div>
          <dt>已发布标的</dt>
          <dd>{{ items.length }} 个 · 独立于价格快照</dd>
        </div>
      </dl>
      <div v-if="data.freshness" class="fundamentals-freshness">
        <StatusPill
          :status="data.freshness.snapshot_state"
          :label="data.freshness.snapshot_state === 'stale' ? '本地快照陈旧' : '本地快照新鲜'"
        />
        <span
          >采集已过 {{ data.freshness.snapshot_age_days }} 天 / 阈值
          {{ data.freshness.snapshot_stale_after_days }} 天；来源更新超过
          {{ data.freshness.source_stale_after_days }} 天另行标记。</span
        >
      </div>
      <p v-if="data.freshness?.snapshot_state === 'stale'" class="freshness-warning">
        当前保留上次发布的数值，已超出本地快照新鲜度阈值；不代表同步失败。
      </p>
      <p class="source-note">
        来源更新日期仅描述供应商文件，不等于财报期，也不代表实时估值报价。倍数按来源口径展示，未与当前价格重新对齐。
      </p>
      <div class="fundamentals-columns" aria-hidden="true">
        <span>标的 / 类型</span><span>财务摘要</span><span>估值摘要</span
        ><span>来源更新 / 详情</span>
      </div>
      <ul class="fundamentals-list" aria-label="已发布基本面标的">
        <li v-for="item in items" :key="item.instrument_id" class="fundamentals-row">
          <div class="fundamentals-identity">
            <button
              type="button"
              :aria-label="`查看 ${item.instrument_id} 基本面详情`"
              @click="emit('openStock', item.instrument_id)"
            >
              <strong class="mono">{{ item.instrument_id }}</strong
              ><ArrowUpRight :size="16" aria-hidden="true" />
            </button>
            <StatusPill :status="item.kind" :label="kindLabels[item.kind]" :dot="false" />
            <small>{{ item.industry || item.provider_sector || '来源行业未确认' }}</small>
          </div>
          <template v-if="item.kind === 'operating' || item.kind === 'financial'">
            <div
              v-for="group in ['financial', 'valuation'] as const"
              :key="group"
              class="fundamentals-values"
            >
              <FundamentalMetricValue
                v-for="metric in summaryMetrics(item, group)"
                :key="metric.name"
                :metric="metric"
                compact
              />
            </div>
          </template>
          <div v-else class="fundamentals-not-applicable">
            <strong>{{
              item.kind === 'reit' ? '通用财务与估值指标不适用' : '公司分类未确认'
            }}</strong>
            <p>
              {{
                item.kind === 'reit'
                  ? '未实现 REIT 专用口径，所有指标保留不适用原因。'
                  : '不套用一般企业指标；详情保留全部字段和原因。'
              }}
            </p>
          </div>
          <div class="fundamentals-source">
            <StatusPill
              :status="item.source_update_state"
              :label="sourceLabels[item.source_update_state]"
            />
            <strong class="tabular">{{ item.source_updated_date || '日期未确认' }}</strong>
            <small v-if="item.source_age_days !== null"
              >距查询日 {{ item.source_age_days }} 天</small
            >
            <button type="button" @click="emit('openStock', item.instrument_id)">
              查看全部 7 项 <ArrowUpRight :size="14" aria-hidden="true" />
            </button>
          </div>
        </li>
      </ul>
    </template>
  </section>
</template>

<style scoped>
.fundamentals-panel {
  padding: 0 24px 24px;
}
.fundamentals-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  flex-wrap: wrap;
  gap: 16px;
  margin-bottom: 20px;
}
h3 {
  margin: 4px 0 0;
  font-size: 1.08rem;
  letter-spacing: -0.025em;
}
.fundamentals-header p:last-child,
.source-note {
  color: var(--color-text-soft);
  font-size: 0.73rem;
  line-height: 1.6;
}
.fundamentals-header p:last-child {
  margin: 8px 0 0;
}
.fundamentals-meta {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 18px;
  padding: 18px;
  margin: 0;
  background: var(--color-surface-soft);
  border: 1px solid var(--color-line);
  border-radius: var(--radius-md);
}
.fundamentals-meta dt {
  color: var(--color-text-faint);
  font-size: 0.66rem;
}
.fundamentals-meta dd {
  margin: 7px 0 0;
  font-size: 0.75rem;
  font-weight: 650;
  overflow-wrap: anywhere;
}
.fundamentals-freshness {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 16px;
}
.fundamentals-freshness > span:last-child {
  color: var(--color-text-soft);
  font-size: 0.7rem;
  line-height: 1.6;
}
.freshness-warning {
  padding: 12px 16px;
  color: var(--color-warning);
  background: var(--color-warning-bg);
  border-radius: var(--radius-sm);
  font-size: 0.73rem;
  line-height: 1.6;
}
.source-note {
  margin: 12px 0 22px;
}
.fundamentals-columns,
.fundamentals-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1.15fr) minmax(0, 1.15fr) minmax(0, 1fr);
  gap: 24px;
  padding: 16px 18px;
}
.fundamentals-columns {
  color: var(--color-text-faint);
  font-size: 0.67rem;
}
.fundamentals-list {
  padding: 0;
  margin: 0;
  list-style: none;
  border: 1px solid var(--color-line);
  border-radius: var(--radius-md);
  overflow: hidden;
}
.fundamentals-row {
  padding-top: 22px;
  padding-bottom: 22px;
  border-top: 1px solid var(--color-line);
}
.fundamentals-row:first-child {
  border-top: 0;
}
.fundamentals-identity,
.fundamentals-source,
.fundamentals-values {
  display: grid;
  align-content: start;
  justify-items: start;
  gap: 12px;
  min-width: 0;
}
.fundamentals-values {
  gap: 18px;
}
.fundamentals-identity button,
.fundamentals-source button {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 0;
  background: transparent;
  color: var(--color-text);
  text-align: left;
  cursor: pointer;
}
.fundamentals-identity strong {
  font-size: 0.88rem;
  overflow-wrap: anywhere;
}
.fundamentals-identity button svg {
  flex-shrink: 0;
}
.fundamentals-identity small,
.fundamentals-source small {
  color: var(--color-text-faint);
  font-size: 0.66rem;
  line-height: 1.6;
  overflow-wrap: anywhere;
}
.fundamentals-source strong,
.fundamentals-source button {
  font-size: 0.72rem;
}
.fundamentals-source button {
  color: var(--color-text-soft);
  margin-top: 8px;
}
.fundamentals-not-applicable {
  grid-column: span 2;
  color: var(--color-text-soft);
  font-size: 0.73rem;
  line-height: 1.6;
}
.fundamentals-not-applicable p {
  margin-bottom: 0;
  color: var(--color-text-faint);
}
@media (max-width: 900px) {
  .fundamentals-meta {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .fundamentals-columns {
    display: none;
  }
  .fundamentals-row {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 22px 18px;
  }
  .fundamentals-identity {
    grid-area: 1 / 1;
  }
  .fundamentals-source {
    grid-area: 1 / 2;
  }
}
@media (max-width: 600px) {
  .fundamentals-panel {
    padding: 0 16px 16px;
  }
  .fundamentals-meta,
  .fundamentals-row {
    padding: 16px;
    gap: 20px 14px;
  }
}
</style>

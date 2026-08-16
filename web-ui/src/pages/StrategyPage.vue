<script setup lang="ts">
import { BrainCircuit, CalendarClock, Layers3, ShieldCheck } from '@lucide/vue'
import { useQuery } from '@tanstack/vue-query'
import { computed } from 'vue'

import { factorQuery, strategyQuery } from '../api/queries'
import DataChart from '../components/DataChart.vue'
import DataState from '../components/DataState.vue'
import DataTable from '../components/DataTable.vue'
import MetricCard from '../components/MetricCard.vue'
import StatusPill from '../components/StatusPill.vue'
import {
  formatDateTime,
  formatLabel,
  formatNumber,
  formatPercent,
  formatScalar,
  formatSourceState,
  formatStrategyName,
} from '../utils/format'

const strategy = useQuery(strategyQuery())
const factor = useQuery(factorQuery())

const selectedCount = computed(
  () => factor.data.value?.scores.filter((score) => score.selected).length ?? 0,
)
const eligibleCount = computed(
  () => factor.data.value?.scores.filter((score) => score.eligible).length ?? 0,
)
const factorPoints = computed(
  () =>
    factor.data.value?.scores
      .filter((score) => score.eligible)
      .map((score) => ({
        label: score.symbol,
        value: score.score,
        emphasis: score.selected,
      })) ?? [],
)

async function retryStrategy(): Promise<void> {
  await strategy.refetch()
}

async function retryFactor(): Promise<void> {
  await factor.refetch()
}
</script>

<template>
  <div class="page-stack">
    <div class="page-intro">
      <div>
        <h1>策略与因子</h1>
        <p>
          展示当前装配的策略配置和最近一个完整因子横截面；这里不重新计算模型，也不评价因子有效性。
        </p>
      </div>
      <StatusPill
        v-if="strategy.data.value"
        :status="strategy.data.value.source_state"
        :label="formatSourceState(strategy.data.value.source_state)"
      />
    </div>

    <DataState v-if="strategy.isPending.value" state="loading" />
    <DataState
      v-else-if="strategy.isError.value"
      state="error"
      :detail="strategy.error.value?.message"
      retry-label="重新读取策略"
      @retry="retryStrategy"
    />
    <template v-else-if="strategy.data.value">
      <DataState
        v-if="strategy.data.value.source_state !== 'available'"
        :state="strategy.data.value.source_state"
        title="活动策略配置不可用"
        detail="页面不会根据历史信号反推当前策略配置。"
      />

      <section class="strategy-hero surface">
        <div class="strategy-identity">
          <div class="identity-icon" aria-hidden="true">
            <BrainCircuit :size="25" :stroke-width="1.6" />
          </div>
          <div>
            <p class="eyebrow">Active strategy</p>
            <h2>{{ formatStrategyName(strategy.data.value.name) }}</h2>
            <p>配置观测于 {{ formatDateTime(strategy.data.value.observed_at_utc) }}</p>
          </div>
        </div>
        <dl class="strategy-context">
          <div>
            <dt><ShieldCheck :size="15" aria-hidden="true" />审批模式</dt>
            <dd>{{ strategy.data.value.approval_mode || '—' }}</dd>
          </div>
          <div>
            <dt><CalendarClock :size="15" aria-hidden="true" />信号有效期</dt>
            <dd>
              {{
                strategy.data.value.signal_expiry_hours === null
                  ? '—'
                  : `${String(strategy.data.value.signal_expiry_hours)} 小时`
              }}
            </dd>
          </div>
          <div>
            <dt><Layers3 :size="15" aria-hidden="true" />参数 / 风控</dt>
            <dd>
              {{ Object.keys(strategy.data.value.parameters).length }} /
              {{ Object.keys(strategy.data.value.risk_limits).length }} 项
            </dd>
          </div>
        </dl>
      </section>

      <section class="config-grid">
        <article class="surface config-card">
          <div class="section-header">
            <div>
              <p class="eyebrow">Model settings</p>
              <h2>策略参数</h2>
            </div>
          </div>
          <dl v-if="Object.keys(strategy.data.value.parameters).length" class="key-value-list">
            <div v-for="(value, key) in strategy.data.value.parameters" :key="key">
              <dt>{{ formatLabel(key) }}</dt>
              <dd class="tabular">{{ formatScalar(value) }}</dd>
            </div>
          </dl>
          <DataState v-else state="empty" title="没有策略参数" detail="当前配置未暴露额外参数。" />
        </article>

        <article class="surface config-card">
          <div class="section-header">
            <div>
              <p class="eyebrow">Risk envelope</p>
              <h2>统一风控阈值</h2>
            </div>
          </div>
          <dl v-if="Object.keys(strategy.data.value.risk_limits).length" class="key-value-list">
            <div v-for="(value, key) in strategy.data.value.risk_limits" :key="key">
              <dt>{{ formatLabel(key) }}</dt>
              <dd class="tabular">{{ formatScalar(value) }}</dd>
            </div>
          </dl>
          <DataState
            v-else
            state="empty"
            title="没有风控配置"
            detail="当前只读配置中没有可展示阈值。"
          />
        </article>
      </section>
    </template>

    <section class="factor-section surface">
      <div class="factor-header">
        <div>
          <p class="eyebrow">Latest complete batch</p>
          <h2>PatchTST 因子横截面</h2>
          <p v-if="factor.data.value">
            因子日期 {{ factor.data.value.asof_date || '—' }} · 可用于交易链路
            {{ formatDateTime(factor.data.value.available_at_utc) }}
          </p>
        </div>
        <StatusPill
          v-if="factor.data.value"
          :status="factor.data.value.source_state"
          :label="formatSourceState(factor.data.value.source_state)"
        />
      </div>

      <DataState v-if="factor.isPending.value" state="loading" />
      <DataState
        v-else-if="factor.isError.value"
        state="error"
        :detail="factor.error.value?.message"
        retry-label="重新读取因子"
        @retry="retryFactor"
      />
      <DataState
        v-else-if="factor.data.value?.source_state !== 'available'"
        :state="factor.data.value?.source_state || 'empty'"
        title="完整因子批次不可用"
        detail="仅有原子完整批次才会进入这个视图。"
      />
      <template v-else-if="factor.data.value">
        <section class="metric-grid factor-metrics" aria-label="因子批次摘要">
          <MetricCard
            label="完整性"
            :value="`${String(factor.data.value.scores.length)} / ${String(factor.data.value.expected_rows)}`"
            helper="实际读取 / 批次声明行数"
            accent
          />
          <MetricCard label="可选标的" :value="String(eligibleCount)" helper="eligible = true" />
          <MetricCard label="目标持仓" :value="String(selectedCount)" helper="策略权重非零标的" />
        </section>

        <div class="factor-context">
          <div>
            <span>模型发布</span>
            <strong class="mono">{{ factor.data.value.model_release_id || '—' }}</strong>
          </div>
          <div>
            <span>批次</span>
            <strong class="mono">{{ factor.data.value.batch_id || '—' }}</strong>
          </div>
          <div>
            <span>交付</span>
            <strong class="mono">{{ factor.data.value.delivery_id || '—' }}</strong>
          </div>
          <div>
            <span>来源</span>
            <strong>{{ factor.data.value.source_kind || '—' }}</strong>
          </div>
        </div>

        <div class="factor-chart">
          <div>
            <p class="eyebrow">Cross-sectional rank</p>
            <h3>可选标的得分</h3>
            <p>紫色表示进入当前目标组合，灰色表示仅参与本批次排名。</p>
          </div>
          <DataChart
            v-if="factorPoints.length"
            title="因子横截面得分"
            kind="bar"
            :points="factorPoints"
            :height="Math.max(260, factorPoints.length * 36)"
          />
          <DataState
            v-else
            state="empty"
            title="没有可选标的"
            detail="批次存在，但没有 eligible 标的。"
          />
        </div>

        <DataTable caption="最新因子批次逐标的得分" min-width="960px">
          <thead>
            <tr>
              <th>排名 / 标的</th>
              <th class="align-right">得分</th>
              <th>可选</th>
              <th>目标组合</th>
              <th class="align-right">目标权重</th>
              <th>Security ID</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="score in factor.data.value.scores" :key="score.canonical_id">
              <td>
                <div class="cell-stack">
                  <span class="cell-primary">#{{ score.rank ?? '—' }} · {{ score.symbol }}</span>
                  <span class="cell-subtle mono">{{ score.canonical_id }}</span>
                </div>
              </td>
              <td class="align-right cell-primary tabular">{{ formatNumber(score.score, 6) }}</td>
              <td>
                <StatusPill
                  :status="score.eligible ? 'ok' : 'unobserved'"
                  :label="score.eligible ? '可选' : '排除'"
                />
              </td>
              <td>
                <StatusPill
                  :status="score.selected ? 'approved' : 'unobserved'"
                  :label="score.selected ? '已选中' : '未选中'"
                />
              </td>
              <td class="align-right tabular">{{ formatPercent(score.target_weight) }}</td>
              <td class="mono">{{ score.security_id }}</td>
            </tr>
          </tbody>
        </DataTable>
      </template>
    </section>
  </div>
</template>

<style scoped>
.strategy-hero {
  display: grid;
  min-height: 190px;
  padding: 30px;
  grid-template-columns: minmax(0, 1fr) minmax(380px, 0.8fr);
}

.strategy-identity {
  display: flex;
  align-items: center;
  gap: 19px;
}

.identity-icon {
  display: grid;
  flex: 0 0 auto;
  width: 58px;
  height: 58px;
  color: #fff;
  background: var(--color-surface-strong);
  border-radius: 19px;
  place-items: center;
}

.strategy-identity h2 {
  margin: 0;
  font-size: clamp(1.8rem, 3.8vw, 3.1rem);
  letter-spacing: -0.055em;
}

.strategy-identity p:last-child {
  margin: 10px 0 0;
  color: var(--color-text-faint);
  font-size: 0.72rem;
}

.strategy-context {
  display: grid;
  align-content: center;
  gap: 16px;
  padding-left: 30px;
  margin: 0;
  border-left: 1px solid var(--color-line);
}

.strategy-context div,
.key-value-list div {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
}

.strategy-context dt {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  color: var(--color-text-soft);
  font-size: 0.74rem;
}

.strategy-context dd,
.key-value-list dd {
  margin: 0;
  color: var(--color-text);
  font-size: 0.78rem;
  font-weight: 650;
}

.config-grid {
  display: grid;
  gap: 18px;
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.config-card {
  padding: 24px;
}

.key-value-list {
  display: grid;
  gap: 0;
  margin: 0;
}

.key-value-list div {
  min-height: 42px;
  border-top: 1px solid var(--color-line);
}

.key-value-list dt {
  color: var(--color-text-soft);
  font-size: 0.72rem;
}

.config-card :deep(.data-state) {
  min-height: 110px;
}

.factor-section {
  padding-top: 26px;
}

.factor-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 20px;
  padding: 0 26px 22px;
}

.factor-header h2,
.factor-chart h3 {
  margin: 0;
  letter-spacing: -0.025em;
}

.factor-header h2 {
  font-size: 1.16rem;
}

.factor-header p:last-child,
.factor-chart p:last-child {
  margin: 7px 0 0;
  color: var(--color-text-soft);
  font-size: 0.76rem;
}

.factor-section > :deep(.data-state) {
  margin: 0 26px 26px;
}

.factor-metrics {
  padding: 0 26px 20px;
}

.factor-context {
  display: grid;
  gap: 1px;
  margin: 0 26px 22px;
  overflow: hidden;
  background: var(--color-line);
  border: 1px solid var(--color-line);
  border-radius: var(--radius-md);
  grid-template-columns: repeat(4, minmax(0, 1fr));
}

.factor-context div {
  display: grid;
  gap: 7px;
  min-width: 0;
  padding: 14px;
  background: var(--color-surface-soft);
}

.factor-context span {
  color: var(--color-text-faint);
  font-size: 0.64rem;
  text-transform: uppercase;
}

.factor-context strong {
  overflow: hidden;
  font-size: 0.7rem;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.factor-chart {
  display: grid;
  gap: 24px;
  padding: 26px;
  border-top: 1px solid var(--color-line);
  grid-template-columns: 240px minmax(0, 1fr);
}

@media (max-width: 980px) {
  .strategy-hero,
  .factor-chart {
    grid-template-columns: 1fr;
  }

  .strategy-context {
    padding: 26px 0 0;
    margin-top: 26px;
    border-top: 1px solid var(--color-line);
    border-left: 0;
  }

  .factor-context {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 660px) {
  .strategy-hero,
  .config-grid {
    display: grid;
    grid-template-columns: 1fr;
  }

  .strategy-hero,
  .config-card {
    padding: 20px;
  }

  .strategy-identity {
    align-items: flex-start;
  }

  .factor-header {
    display: grid;
    padding-right: 20px;
    padding-left: 20px;
  }

  .factor-metrics {
    padding-right: 20px;
    padding-left: 20px;
  }

  .factor-context {
    margin-right: 20px;
    margin-left: 20px;
    grid-template-columns: 1fr;
  }

  .factor-chart {
    padding: 20px;
  }
}
</style>

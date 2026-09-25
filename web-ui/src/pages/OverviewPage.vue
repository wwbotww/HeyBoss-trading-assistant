<script setup lang="ts">
import { ArrowUpRight, Clock3, Database, ShieldCheck, Sparkles } from '@lucide/vue'
import { useQuery } from '@tanstack/vue-query'
import { computed } from 'vue'
import { RouterLink } from 'vue-router'

import { overviewQuery } from '../api/queries'
import DataState from '../components/DataState.vue'
import MetricCard from '../components/MetricCard.vue'
import StatusPill from '../components/StatusPill.vue'
import {
  formatAge,
  formatCurrency,
  formatDateTime,
  formatSourceState,
  formatStatus,
  formatStrategyName,
} from '../utils/format'

const {
  data: overviewData,
  isPending: overviewPending,
  isError: overviewError,
  error,
  refetch,
} = useQuery(overviewQuery())

const portfolio = computed(() => overviewData.value?.portfolio)
const positionsConfirmed = computed(() => {
  const snapshot = portfolio.value
  return (
    snapshot?.source_state === 'available' &&
    !snapshot.is_stale &&
    snapshot.broker_connected === true &&
    snapshot.reconciliation_complete === true &&
    Boolean(snapshot.account_updated_at_utc) &&
    !snapshot.not_ready_reason
  )
})
const workflowCounts = computed(() => {
  const counts = overviewData.value?.workflow_status_counts ?? {}
  return Object.entries(counts).sort((left, right) => right[1] - left[1])
})

async function retry(): Promise<void> {
  await refetch()
}
</script>

<template>
  <div class="page-stack">
    <div class="page-intro">
      <div>
        <h1>今天，一眼看清。</h1>
        <p>账户、策略和执行活动来自同一套只读事实，不推断实时行情，也不创建第二条交易路径。</p>
      </div>
      <span v-if="overviewData" class="observed-at">
        观测于 {{ formatDateTime(overviewData.observed_at_utc) }}
      </span>
    </div>

    <DataState v-if="overviewPending" state="loading" />
    <DataState
      v-else-if="overviewError"
      state="error"
      :detail="error?.message"
      retry-label="重新读取"
      @retry="retry"
    />

    <template v-else-if="overviewData && portfolio">
      <section class="hero surface" aria-labelledby="asset-heading">
        <div class="hero-main">
          <div class="hero-label-row">
            <p id="asset-heading" class="eyebrow">Net liquidation</p>
            <StatusPill
              :status="portfolio.is_stale ? 'new' : portfolio.source_state"
              :label="portfolio.is_stale ? '账户未就绪' : formatSourceState(portfolio.source_state)"
            />
          </div>
          <p class="asset-value tabular">
            {{ formatCurrency(portfolio.net_liquidation, portfolio.currency) }}
          </p>
          <div class="asset-context">
            <span>{{ portfolio.account_id || '未发现账户快照' }}</span>
            <span>{{ portfolio.currency || 'USD' }} · 账户快照</span>
            <span>{{ formatAge(portfolio.age_seconds) }}</span>
          </div>
        </div>
        <div class="hero-side">
          <div class="brand-orbit" aria-hidden="true">
            <span></span>
            <span></span>
          </div>
          <div>
            <p>当前环境</p>
            <strong>IBKR Paper</strong>
            <small>只读操作台</small>
          </div>
        </div>
      </section>

      <DataState
        v-if="portfolio.source_state !== 'available'"
        :state="portfolio.source_state"
        title="账户快照不可用"
        detail="净值和现金字段不会由前端估算；请以当前数据源状态为准。"
      />

      <section class="metric-grid" aria-label="账户关键指标">
        <MetricCard
          label="可用资金"
          :value="formatCurrency(portfolio.available_funds, portfolio.currency)"
          helper="券商报告的可用资金; 不等同于现金"
          accent
        />
        <MetricCard
          label="现金余额"
          :value="formatCurrency(portfolio.total_cash_value, portfolio.currency)"
          helper="券商报告的现金余额"
        />
        <MetricCard
          label="当前持仓"
          :value="positionsConfirmed ? String(portfolio.positions.length) : '待确认'"
          :helper="
            positionsConfirmed
              ? '最近完整账户快照'
              : `历史快照记录 ${portfolio.positions.length} 项 · ${formatDateTime(portfolio.snapshot_at_utc)}`
          "
        />
      </section>
      <p v-if="portfolio.not_ready_reason" role="status">
        账户未就绪：{{ portfolio.not_ready_reason }}
      </p>

      <section class="overview-grid">
        <article class="surface section-card decision-card">
          <div class="section-header">
            <div>
              <p class="eyebrow">Decision queue</p>
              <h2>交易工作流</h2>
              <p>从信号生成到审批、风控和成交的状态汇总。</p>
            </div>
            <RouterLink class="icon-link" to="/activity" aria-label="查看全部决策流">
              <ArrowUpRight :size="17" aria-hidden="true" />
            </RouterLink>
          </div>

          <div v-if="workflowCounts.length" class="workflow-counts">
            <div v-for="entry in workflowCounts" :key="entry[0]">
              <StatusPill :status="entry[0]" :label="formatStatus(entry[0])" />
              <strong class="tabular">{{ entry[1] }}</strong>
            </div>
          </div>
          <DataState
            v-else
            state="empty"
            title="暂无工作流"
            detail="当前审计库里还没有交易工作流。"
          />

          <dl class="activity-times">
            <div>
              <dt><Clock3 :size="15" aria-hidden="true" />最近工作流</dt>
              <dd>{{ formatDateTime(overviewData.latest_workflow_at_utc) }}</dd>
            </div>
            <div>
              <dt><Clock3 :size="15" aria-hidden="true" />最近成交</dt>
              <dd>{{ formatDateTime(overviewData.latest_fill_at_utc) }}</dd>
            </div>
          </dl>
        </article>

        <article class="surface section-card intelligence-card">
          <div class="section-header">
            <div>
              <p class="eyebrow">Strategy context</p>
              <h2>当前决策模型</h2>
              <p>仅展示配置和最近完整因子批次，不评估因子有效性。</p>
            </div>
            <RouterLink class="icon-link" to="/strategy" aria-label="查看策略与因子">
              <Sparkles :size="19" :stroke-width="1.7" aria-hidden="true" />
            </RouterLink>
          </div>

          <div class="strategy-name">
            <span>Active strategy</span>
            <strong>{{ formatStrategyName(overviewData.active_strategy.name) }}</strong>
            <StatusPill
              :status="overviewData.active_strategy.source_state"
              :label="formatSourceState(overviewData.active_strategy.source_state)"
            />
          </div>

          <div class="context-list">
            <div>
              <span><ShieldCheck :size="15" aria-hidden="true" />审批模式</span>
              <strong>{{ overviewData.active_strategy.approval_mode || '—' }}</strong>
            </div>
            <div>
              <span><Database :size="15" aria-hidden="true" />因子日期</span>
              <strong>{{ overviewData.latest_factor.asof_date || '—' }}</strong>
            </div>
            <div>
              <span>模型发布</span>
              <strong class="mono">{{ overviewData.latest_factor.model_release_id || '—' }}</strong>
            </div>
            <div>
              <span>完整横截面</span>
              <strong>{{ overviewData.latest_factor.expected_rows }} 条</strong>
            </div>
          </div>

          <RouterLink class="quality-row" to="/system">
            <span>数据质量</span>
            <StatusPill
              :status="overviewData.data_quality_state"
              :label="formatSourceState(overviewData.data_quality_state)"
            />
          </RouterLink>
        </article>
      </section>
    </template>
  </div>
</template>

<style scoped>
.observed-at {
  color: var(--color-text-faint);
  font-size: 0.68rem;
  white-space: nowrap;
}

.hero {
  position: relative;
  display: grid;
  min-height: 270px;
  color: #fff;
  background: var(--color-surface-strong);
  grid-template-columns: minmax(0, 1fr) 270px;
}

.hero::before {
  position: absolute;
  top: 0;
  left: 32px;
  width: 118px;
  height: 4px;
  content: '';
  background: var(--gradient-brand);
  border-radius: 0 0 5px 5px;
}

.hero-main {
  display: grid;
  align-content: center;
  padding: 42px;
}

.hero-label-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
}

.hero .eyebrow {
  color: #8d9099;
}

.asset-value {
  margin: 16px 0 20px;
  font-size: clamp(2.6rem, 6.3vw, 5.25rem);
  font-weight: 590;
  line-height: 0.96;
  letter-spacing: -0.072em;
}

.asset-context {
  display: flex;
  flex-wrap: wrap;
  gap: 9px 22px;
  color: #a8abb2;
  font-size: 0.72rem;
}

.hero-side {
  display: grid;
  align-content: center;
  justify-items: center;
  padding: 34px;
  text-align: center;
  border-left: 1px solid #2a2c31;
}

.brand-orbit {
  position: relative;
  width: 96px;
  height: 96px;
  margin-bottom: 20px;
  background: #1d1f24;
  border: 1px solid #303238;
  border-radius: 50%;
}

.brand-orbit::before,
.brand-orbit::after,
.brand-orbit span {
  position: absolute;
  content: '';
  border-radius: 50%;
}

.brand-orbit::before {
  inset: 20px;
  background: var(--gradient-brand);
  filter: saturate(112%);
}

.brand-orbit::after {
  inset: 35px;
  background: #fff;
}

.brand-orbit span:first-child {
  top: 9px;
  left: 46px;
  width: 7px;
  height: 7px;
  background: #fff;
}

.brand-orbit span:last-child {
  right: 11px;
  bottom: 18px;
  width: 5px;
  height: 5px;
  background: var(--color-brand-pink);
}

.hero-side p,
.hero-side small {
  margin: 0;
  color: #8d9099;
  font-size: 0.67rem;
}

.hero-side strong {
  display: block;
  margin: 6px 0 4px;
  font-size: 0.92rem;
}

.metric-grid {
  display: grid;
  gap: 14px;
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.overview-grid {
  display: grid;
  gap: 18px;
  grid-template-columns: minmax(0, 1.08fr) minmax(360px, 0.92fr);
}

.icon-link {
  display: grid;
  flex: 0 0 auto;
  width: 34px;
  height: 34px;
  color: var(--color-text-soft);
  background: var(--color-surface-soft);
  border: 1px solid var(--color-line);
  border-radius: 50%;
  place-items: center;
}

.workflow-counts {
  display: grid;
  gap: 9px;
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.workflow-counts > div {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 52px;
  padding: 10px 12px;
  background: var(--color-surface-soft);
  border-radius: 12px;
}

.workflow-counts strong {
  font-size: 1rem;
}

.activity-times {
  display: grid;
  gap: 12px;
  padding-top: 20px;
  margin: 20px 0 0;
  border-top: 1px solid var(--color-line);
}

.activity-times > div,
.context-list > div,
.quality-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-width: 0;
  gap: 16px;
}

.activity-times dt,
.context-list span,
.quality-row > span {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  color: var(--color-text-soft);
  font-size: 0.73rem;
}

.activity-times dd {
  margin: 0;
  color: var(--color-text);
  font-size: 0.7rem;
  font-variant-numeric: tabular-nums;
}

.strategy-name {
  display: grid;
  gap: 7px;
  padding: 18px;
  background: var(--color-surface-soft);
  border-radius: var(--radius-md);
}

.strategy-name > span {
  color: var(--color-text-faint);
  font-size: 0.66rem;
  text-transform: uppercase;
  letter-spacing: 0.08em;
}

.strategy-name strong {
  margin-bottom: 4px;
  font-size: 1.28rem;
  letter-spacing: -0.03em;
}

.context-list {
  display: grid;
  gap: 14px;
  padding: 20px 2px;
}

.context-list strong {
  min-width: 0;
  max-width: 55%;
  overflow: hidden;
  font-size: 0.73rem;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.quality-row {
  padding-top: 17px;
  border-top: 1px solid var(--color-line);
}

@media (max-width: 1080px) {
  .overview-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 720px) {
  .hero {
    grid-template-columns: 1fr;
  }

  .hero-main {
    padding: 34px 24px;
  }

  .hero-side {
    display: none;
  }

  .asset-value {
    font-size: clamp(2.4rem, 12vw, 4rem);
  }

  .metric-grid {
    grid-template-columns: 1fr;
  }

  .workflow-counts {
    grid-template-columns: 1fr;
  }

  .activity-times > div {
    display: grid;
    gap: 5px;
  }
}
</style>

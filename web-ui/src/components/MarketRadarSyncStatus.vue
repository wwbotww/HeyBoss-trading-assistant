<script setup lang="ts">
import type { MarketRadarSyncStatus } from '../api/types'
import { formatDateTime } from '../utils/format'
import DataState from './DataState.vue'
import StatusPill from './StatusPill.vue'

defineProps<{
  data?: MarketRadarSyncStatus | undefined
  loading: boolean
  error?: string | undefined
}>()
defineEmits<{ retry: [] }>()

const workers: Record<MarketRadarSyncStatus['worker_state'], string> = {
  not_configured: '每日采集尚未启用',
  running: '正在采集',
  idle: '采集服务在线',
  stopped: '采集服务已停止',
  stale: '采集服务心跳过期',
}
const jobs: Record<string, string> = {
  prices: '固定价格池',
  macro: '宏观象限',
  economic_events: '经济事件',
  fundamentals: '观察股基本面',
  earnings: '盈利与财报',
  breadth: '当前市场宽度',
}
const states: Record<string, string> = {
  pending: '等待采集',
  running: '采集中',
  complete: '目标已更新',
  waiting_source: '等待来源更新',
  waiting_dependency: '等待基准价格',
  failed: '采集失败',
  interrupted: '上次采集中断',
  blocked: '本轮已停止尝试',
  invalid: '状态无法读取',
}
const reasons: Record<string, string> = {
  missing_eodhd_token: '缺少 EODHD 凭据，需要配置后重试',
  missing_fred_key: '缺少 FRED 凭据，需要配置后重试',
  EodhdAuthenticationError: 'EODHD 认证或数据权限不足',
  FredAuthenticationError: 'FRED 认证失败',
  rate_limited: '供应商请求频率或额度受限',
  temporary_source: '供应商暂时不可用',
  price_not_ready: '目标交易日价格尚未齐备',
  real_rate_not_ready: '实际利率数据尚未更新',
  target_not_ready: '目标数据尚未齐备',
  benchmark_not_ready: '基准价格尚未更新',
  catalog_recovery_required: '价格文件写入中断，需要核对',
  data_quality: '来源数据未通过质量检查',
  process_interrupted: '采集进程曾中断',
  source_unreadable: '已保存的数据未通过校验',
  attempt_budget_exhausted: '本轮尝试次数已用完',
}
</script>

<template>
  <section class="radar-sync" aria-label="雷达每日更新状态">
    <DataState
      v-if="error"
      state="error"
      title="更新状态读取失败"
      :detail="error"
      retry-label="重新读取"
      @retry="$emit('retry')"
    />
    <DataState v-else-if="loading && !data" state="loading" title="正在读取更新状态" />
    <details
      v-else-if="data"
      :open="
        data.worker_state === 'stale' ||
        data.jobs.some((job) =>
          ['failed', 'blocked', 'invalid', 'waiting_source'].includes(job.state),
        )
      "
    >
      <summary>
        <strong>每日更新</strong>
        <StatusPill
          :status="data.worker_state === 'idle' ? 'available' : data.worker_state"
          :label="workers[data.worker_state]"
        />
        <span>{{ data.jobs.filter((job) => job.target_satisfied).length }}/6 来源满足目标日期</span>
      </summary>
      <p class="sync-meta">
        最后心跳：{{ formatDateTime(data.last_heartbeat_at_utc) }} · 下次计划：{{
          formatDateTime(data.next_scheduled_at_utc)
        }}
      </p>
      <ul class="sync-jobs">
        <li v-for="job in data.jobs" :key="job.job">
          <div class="job-title">
            <strong>{{ jobs[job.job] || '数据来源' }}</strong
            ><StatusPill :status="job.state" :label="states[job.state] || '状态待核对'" />
          </div>
          <p>目标 {{ job.target_date }} · 已发布 {{ job.published_as_of_date || '—' }}</p>
          <p>最近成功 {{ formatDateTime(job.last_success_at_utc) }}</p>
          <p>
            最近尝试 {{ formatDateTime(job.last_attempt_at_utc) }} · 本轮 {{ job.attempts_used }} 次
          </p>
          <p v-if="job.next_retry_at_utc">下次尝试 {{ formatDateTime(job.next_retry_at_utc) }}</p>
          <p v-if="job.error_code" class="sync-warning">
            {{ reasons[job.error_code] || '配置或来源异常，请检查采集记录' }}
          </p>
          <p v-if="job.data_validity && job.data_validity !== 'complete'">
            部分指标仍受覆盖率或历史长度限制，详见下方模块。
          </p>
        </li>
      </ul>
      <p class="sync-meta">页面每分钟重新读取可见视图；数据日期与采集进程状态分别判断。</p>
    </details>
  </section>
</template>

<style scoped>
.radar-sync {
  margin-block: 16px;
  padding: 16px;
  background: var(--color-surface-soft);
  border: 1px solid var(--color-line);
  border-radius: var(--radius-md);
}
summary {
  cursor: pointer;
  line-height: 2;
}
summary > * {
  margin-right: 14px;
}
summary > span,
.sync-meta,
.sync-jobs p {
  color: var(--color-text-soft);
  font-size: 0.75rem;
  line-height: 1.6;
}
.sync-jobs {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 12px;
  padding: 0;
  list-style: none;
}
.sync-jobs li {
  padding: 12px;
  background: var(--color-surface);
  border: 1px solid var(--color-line);
  border-radius: 8px;
}
.job-title {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  font-size: 0.8rem;
}
.sync-jobs p {
  margin: 6px 0 0;
}
.sync-jobs .sync-warning {
  color: var(--color-warning);
}
</style>

<script setup lang="ts">
import { computed } from 'vue'

import type { FundamentalMetric } from '../api/types'
import { formatNumber, formatPercent } from '../utils/format'

const props = withDefaults(defineProps<{ metric: FundamentalMetric; compact?: boolean }>(), {
  compact: false,
})

const definitions: Readonly<
  Record<FundamentalMetric['name'], { label: string; percent: boolean }>
> = {
  fcf_margin: { label: 'FCF 利润率 · TTM', percent: true },
  net_debt_to_ebitda: { label: '净债务 / EBITDA · TTM', percent: false },
  fcf_yield: { label: 'FCF 收益率 · TTM / 当前市值', percent: true },
  forward_pe: { label: 'Forward P/E · 来源口径', percent: false },
  enterprise_value_to_ebitda: { label: 'EV / EBITDA · 来源口径', percent: false },
  return_on_equity_ttm: { label: 'ROE · TTM · 来源口径', percent: true },
  price_to_book: { label: 'P/B · 来源口径', percent: false },
}
const reasons: Readonly<Record<NonNullable<FundamentalMetric['reason']>, string>> = {
  missing_field: '来源字段缺失',
  insufficient_history: '不足四个季度，无法形成 TTM',
  non_contiguous_periods: '财报季度不连续',
  period_mismatch: '财报期间未对齐',
  missing_currency: '财报币种未确认',
  currency_mismatch: '财报与上市币种不一致',
  invalid_denominator: '分母非正或不可用',
  non_positive_multiple: '来源倍数非正',
  non_finite_result: '计算结果不是有限数值',
  not_applicable: '不适用于该公司类型',
  unknown_classification: '公司分类未确认',
}
const definition = computed(() => definitions[props.metric.name])
const value = computed(() => {
  if (props.metric.value === null) return '—'
  return definition.value.percent
    ? formatPercent(props.metric.value)
    : `${formatNumber(props.metric.value)}×`
})
</script>

<template>
  <div class="fundamental-metric" :class="{ 'fundamental-metric--compact': compact }">
    <span>{{ definition.label }}</span>
    <strong class="tabular">{{ value }}</strong>
    <small v-if="metric.reason" class="metric-reason">{{ reasons[metric.reason] }}</small>
    <small>{{ metric.period_end ? `报告期 ${metric.period_end}` : '报告期未确认' }}</small>
  </div>
</template>

<style scoped>
.fundamental-metric {
  display: grid;
  align-content: start;
  gap: 7px;
  min-width: 0;
  overflow-wrap: anywhere;
}
.fundamental-metric > span {
  color: var(--color-text-soft);
  font-size: 0.71rem;
  line-height: 1.5;
}
.fundamental-metric > strong {
  color: var(--color-text);
  font-size: 1.55rem;
  font-weight: 630;
  letter-spacing: -0.035em;
}
.fundamental-metric > small {
  color: var(--color-text-faint);
  font-size: 0.66rem;
  line-height: 1.5;
}
.fundamental-metric > .metric-reason {
  color: var(--color-text-soft);
}
.fundamental-metric--compact {
  gap: 4px;
}
.fundamental-metric--compact > strong {
  font-size: 1.08rem;
}
</style>

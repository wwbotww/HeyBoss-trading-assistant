<script setup lang="ts">
import { computed } from 'vue'

import type { RadarMetric } from '../api/types'
import { formatNumber, formatPercent } from '../utils/format'

const props = withDefaults(
  defineProps<{
    metric: RadarMetric
    unit: 'percent' | 'percentage_points'
    colorMode: 'directional' | 'neutral'
    compact?: boolean
  }>(),
  {
    compact: false,
  },
)

const tone = computed(() => {
  if (props.metric.value === null) {
    return 'muted'
  }
  if (props.colorMode === 'neutral' || props.metric.value === 0) return 'neutral'
  return props.metric.value > 0 ? 'positive' : 'negative'
})

const value = computed(() => {
  if (props.metric.value === null) return '—'
  return props.unit === 'percentage_points'
    ? `${formatNumber(props.metric.value * 100)} 个百分点`
    : formatPercent(props.metric.value)
})

const detail = computed(() => {
  if (props.metric.validity === 'unavailable') return '数据不可用'
  const history = `可用历史 ${String(props.metric.observations)} 根日线`
  if (props.compact && props.metric.validity === 'complete') return history
  return `${history} / 最低需要 ${String(props.metric.required)} 根${props.unit === 'percentage_points' ? '（两侧历史取较短值）' : ''}`
})
</script>

<template>
  <div
    class="radar-metric"
    :class="[`radar-metric--${tone}`, { 'radar-metric--compact': compact }]"
  >
    <strong class="tabular">{{ value }}</strong>
    <span>{{ detail }}</span>
  </div>
</template>

<style scoped>
.radar-metric {
  display: grid;
  gap: 4px;
}

.radar-metric strong {
  color: var(--color-text);
  font-size: 1rem;
  letter-spacing: -0.025em;
}

.radar-metric span {
  color: var(--color-text-faint);
  font-size: 0.66rem;
}

.radar-metric--compact {
  display: inline-grid;
  min-width: 92px;
}

.radar-metric--compact strong {
  font-size: 0.79rem;
}

.radar-metric--positive strong {
  color: var(--color-positive);
}

.radar-metric--negative strong {
  color: var(--color-negative);
}

.radar-metric--muted strong {
  color: var(--color-text-faint);
}
</style>

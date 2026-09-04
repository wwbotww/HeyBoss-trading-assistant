<script setup lang="ts">
import { computed } from 'vue'

import type { RadarMetric } from '../api/types'
import { formatPercent } from '../utils/format'

const props = withDefaults(
  defineProps<{
    metric: RadarMetric
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
  return props.metric.value >= 0 ? 'positive' : 'negative'
})

const detail = computed(() => {
  if (props.metric.validity === 'complete') {
    return `${String(props.metric.observations)} 个观测`
  }
  if (props.metric.validity === 'insufficient_history') {
    return `历史 ${String(props.metric.observations)} / ${String(props.metric.required)}`
  }
  return '数据不可用'
})
</script>

<template>
  <div
    class="radar-metric"
    :class="[`radar-metric--${tone}`, { 'radar-metric--compact': compact }]"
  >
    <strong class="tabular">{{ formatPercent(metric.value) }}</strong>
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

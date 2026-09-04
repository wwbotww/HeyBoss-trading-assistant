<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    status: string
    label?: string
    dot?: boolean
  }>(),
  {
    label: '',
    dot: true,
  },
)

const tone = computed(() => {
  const status = props.status.toLowerCase()
  if (
    ['available', 'ok', 'filled', 'approved', 'accepted', 'executed', 'complete'].includes(status)
  ) {
    return 'positive'
  }
  if (['failed', 'rejected', 'denied', 'invalid', 'cancelled'].includes(status)) {
    return 'negative'
  }
  if (
    [
      'new',
      'pending',
      'pending_approval',
      'submitted',
      'planned',
      'partially_filled',
      'partial',
      'stale',
      'insufficient_history',
      'insufficient_coverage',
    ].includes(status)
  ) {
    return 'warning'
  }
  if (['buy', 'long'].includes(status)) {
    return 'info'
  }
  return 'neutral'
})
</script>

<template>
  <span class="status-pill" :class="`status-pill--${tone}`">
    <span v-if="dot" class="status-dot" aria-hidden="true"></span>
    {{ label || status }}
  </span>
</template>

<style scoped>
.status-pill {
  display: inline-flex;
  align-items: center;
  width: fit-content;
  min-height: 26px;
  padding: 4px 9px;
  color: var(--color-text-soft);
  font-size: 0.69rem;
  font-weight: 650;
  line-height: 1;
  white-space: nowrap;
  background: var(--color-surface-soft);
  border: 1px solid var(--color-line);
  border-radius: 999px;
}

.status-dot {
  width: 6px;
  height: 6px;
  margin-right: 6px;
  background: currentColor;
  border-radius: 50%;
}

.status-pill--positive {
  color: var(--color-positive);
  background: var(--color-positive-bg);
  border-color: transparent;
}

.status-pill--warning {
  color: var(--color-warning);
  background: var(--color-warning-bg);
  border-color: transparent;
}

.status-pill--negative {
  color: var(--color-negative);
  background: var(--color-negative-bg);
  border-color: transparent;
}

.status-pill--info {
  color: var(--color-info);
  background: var(--color-info-bg);
  border-color: transparent;
}
</style>

<script setup lang="ts">
withDefaults(
  defineProps<{
    label: string
    value: string
    helper?: string
    accent?: boolean
  }>(),
  {
    helper: '',
    accent: false,
  },
)
</script>

<template>
  <article class="metric-card" :class="{ 'metric-card--accent': accent }">
    <p>{{ label }}</p>
    <strong class="tabular">{{ value }}</strong>
    <small v-if="helper">{{ helper }}</small>
  </article>
</template>

<style scoped>
.metric-card {
  position: relative;
  display: grid;
  align-content: start;
  min-height: 128px;
  padding: 20px;
  overflow: hidden;
  background: var(--color-surface);
  border: 1px solid var(--color-line);
  border-radius: var(--radius-md);
}

.metric-card--accent::before {
  position: absolute;
  top: 0;
  right: 0;
  left: 0;
  height: 3px;
  content: '';
  background: var(--gradient-brand);
}

.metric-card p {
  margin: 0 0 17px;
  color: var(--color-text-soft);
  font-size: 0.76rem;
  font-weight: 620;
}

.metric-card strong {
  font-size: clamp(1.28rem, 2.2vw, 1.7rem);
  font-weight: 650;
  letter-spacing: -0.04em;
}

.metric-card small {
  margin-top: 7px;
  color: var(--color-text-faint);
  font-size: 0.7rem;
}
</style>

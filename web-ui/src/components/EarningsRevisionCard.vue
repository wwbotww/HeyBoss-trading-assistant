<script setup lang="ts">
import { computed } from 'vue'

import type { EarningsRevisionAggregate } from '../api/types'
import { formatPercent } from '../utils/format'
import StatusPill from './StatusPill.vue'

const props = withDefaults(
  defineProps<{
    title: string
    description: string
    aggregate: EarningsRevisionAggregate
    statusLabel: string
    featured?: boolean
  }>(),
  { featured: false },
)

const coverageWidth = computed(() => {
  const ratio = Math.min(1, Math.max(0, props.aggregate.coverage_ratio))
  return String(ratio * 100).concat('%')
})
</script>

<template>
  <article class="earnings-revision-card" :class="{ 'earnings-revision-card--featured': featured }">
    <header>
      <div>
        <span>{{ title }}</span>
        <small>{{ description }}</small>
      </div>
      <StatusPill :status="aggregate.validity" :label="statusLabel" :dot="false" />
    </header>

    <div class="earnings-revision-card__hero">
      <div>
        <span>修正宽度</span>
        <strong class="tabular">{{ formatPercent(aggregate.breadth) }}</strong>
      </div>
      <div>
        <span>中位修正幅度</span>
        <strong class="tabular">{{ formatPercent(aggregate.median_magnitude) }}</strong>
      </div>
    </div>

    <div class="earnings-revision-card__directions">
      <div class="is-up">
        <span>上调</span><strong class="tabular">{{ aggregate.upward }}</strong>
      </div>
      <div class="is-flat">
        <span>不变</span><strong class="tabular">{{ aggregate.unchanged }}</strong>
      </div>
      <div class="is-down">
        <span>下调</span><strong class="tabular">{{ aggregate.downward }}</strong>
      </div>
    </div>

    <footer>
      <div class="earnings-revision-card__coverage-label">
        <span>有效覆盖</span>
        <strong class="tabular">
          {{ aggregate.observed }} / {{ aggregate.eligible }} ·
          {{ formatPercent(aggregate.coverage_ratio) }}
        </strong>
      </div>
      <div class="earnings-revision-card__coverage-track" aria-hidden="true">
        <span :style="{ width: coverageWidth }"></span>
      </div>
      <small>
        幅度样本 {{ aggregate.magnitude_observed }}；非正或近零基准
        {{ aggregate.non_positive_or_near_zero }}。
      </small>
    </footer>
  </article>
</template>

<style scoped>
.earnings-revision-card {
  display: grid;
  gap: 22px;
  min-width: 0;
  padding: 22px;
  background: var(--color-surface-soft);
  border: 1px solid var(--color-line);
  border-radius: var(--radius-md);
}

.earnings-revision-card--featured {
  background:
    radial-gradient(circle at 95% 5%, rgb(111 74 255 / 10%), transparent 42%),
    var(--color-surface-soft);
  border-color: color-mix(in srgb, #6f4aff 20%, var(--color-line));
}

header,
.earnings-revision-card__coverage-label,
.earnings-revision-card__directions > div {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

header > div {
  display: grid;
  gap: 5px;
}

header span {
  font-size: 0.8rem;
  font-weight: 700;
}

header small,
footer > small {
  color: var(--color-text-faint);
  font-size: 0.64rem;
  line-height: 1.5;
}

.earnings-revision-card__hero {
  display: grid;
  grid-template-columns: minmax(0, 1.15fr) minmax(0, 0.85fr);
}

.earnings-revision-card__hero > div {
  display: grid;
  gap: 9px;
}

.earnings-revision-card__hero > div + div {
  padding-left: 20px;
  border-left: 1px solid var(--color-line);
}

.earnings-revision-card__hero span,
.earnings-revision-card__directions span,
.earnings-revision-card__coverage-label span {
  color: var(--color-text-faint);
  font-size: 0.66rem;
}

.earnings-revision-card__hero strong {
  font-size: clamp(1.55rem, 2.7vw, 2.65rem);
  font-weight: 640;
  line-height: 1;
  letter-spacing: -0.055em;
}

.earnings-revision-card__hero > div + div strong {
  font-size: clamp(1.2rem, 2vw, 1.8rem);
}

.earnings-revision-card__directions {
  display: grid;
  gap: 8px;
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.earnings-revision-card__directions > div {
  padding: 10px 12px;
  background: var(--color-surface);
  border: 1px solid var(--color-line);
  border-radius: 10px;
}

.earnings-revision-card__directions strong {
  font-size: 0.82rem;
}

.earnings-revision-card__directions .is-up strong {
  color: var(--color-positive);
}

.earnings-revision-card__directions .is-down strong {
  color: var(--color-negative);
}

footer {
  display: grid;
  gap: 9px;
}

.earnings-revision-card__coverage-label strong {
  font-size: 0.68rem;
}

.earnings-revision-card__coverage-track {
  height: 5px;
  overflow: hidden;
  background: var(--color-line);
  border-radius: 999px;
}

.earnings-revision-card__coverage-track span {
  display: block;
  height: 100%;
  background: var(--gradient-brand);
  border-radius: inherit;
}

@media (max-width: 520px) {
  .earnings-revision-card__hero,
  .earnings-revision-card__directions {
    grid-template-columns: 1fr;
  }

  .earnings-revision-card__hero > div + div {
    padding-top: 16px;
    padding-left: 0;
    border-top: 1px solid var(--color-line);
    border-left: 0;
  }
}
</style>

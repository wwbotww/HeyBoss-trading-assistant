<script setup lang="ts">
interface TabItem {
  value: string
  label: string
  count?: number
}

defineProps<{
  modelValue: string
  tabs: readonly TabItem[]
  label: string
}>()

const emit = defineEmits<{
  'update:modelValue': [value: string]
}>()
</script>

<template>
  <div class="segmented-tabs" role="tablist" :aria-label="label">
    <button
      v-for="tab in tabs"
      :key="tab.value"
      type="button"
      role="tab"
      :aria-selected="modelValue === tab.value"
      :class="{ active: modelValue === tab.value }"
      @click="emit('update:modelValue', tab.value)"
    >
      {{ tab.label }}
      <span v-if="tab.count !== undefined">{{ tab.count }}</span>
    </button>
  </div>
</template>

<style scoped>
.segmented-tabs {
  display: inline-flex;
  gap: 4px;
  padding: 4px;
  background: #eceef2;
  border-radius: 999px;
}

.segmented-tabs button {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  min-height: 34px;
  padding: 0 14px;
  color: var(--color-text-soft);
  cursor: pointer;
  background: transparent;
  border-radius: 999px;
  transition:
    color 160ms ease,
    background 160ms ease,
    box-shadow 160ms ease;
}

.segmented-tabs button:hover {
  color: var(--color-text);
}

.segmented-tabs button.active {
  color: var(--color-text);
  background: #fff;
  box-shadow: 0 2px 8px rgb(15 18 27 / 8%);
}

.segmented-tabs span {
  min-width: 20px;
  padding: 2px 6px;
  font-size: 0.67rem;
  font-variant-numeric: tabular-nums;
  background: var(--color-surface-soft);
  border-radius: 999px;
}
</style>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'

interface TabItem {
  value: string
  label: string
  count?: number
}

const props = defineProps<{
  modelValue: string
  tabs: readonly TabItem[]
  label: string
}>()

const emit = defineEmits<{
  'update:modelValue': [value: string]
}>()

const tablist = ref<HTMLElement | null>(null)
const focusedValue = ref(props.modelValue)
const focusEntry = computed(() =>
  props.tabs.some((tab) => tab.value === focusedValue.value)
    ? focusedValue.value
    : (props.tabs.find((tab) => tab.value === props.modelValue)?.value ?? props.tabs[0]?.value),
)

watch(
  () => props.modelValue,
  (value) => {
    focusedValue.value = value
  },
)

function handleKeydown(event: KeyboardEvent, index: number): void {
  if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault()
    const tab = props.tabs[index]
    if (tab) emit('update:modelValue', tab.value)
    return
  }
  let target: number
  if (event.key === 'ArrowRight') target = (index + 1) % props.tabs.length
  else if (event.key === 'ArrowLeft') target = (index - 1 + props.tabs.length) % props.tabs.length
  else if (event.key === 'Home') target = 0
  else if (event.key === 'End') target = props.tabs.length - 1
  else return
  event.preventDefault()
  tablist.value?.querySelectorAll<HTMLButtonElement>('[role="tab"]')[target]?.focus()
}
</script>

<template>
  <div ref="tablist" class="segmented-tabs" role="tablist" :aria-label="label">
    <button
      v-for="(tab, index) in tabs"
      :key="tab.value"
      type="button"
      role="tab"
      :aria-selected="modelValue === tab.value"
      :tabindex="focusEntry === tab.value ? 0 : -1"
      :class="{ active: modelValue === tab.value }"
      @focus="focusedValue = tab.value"
      @keydown="handleKeydown($event, index)"
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

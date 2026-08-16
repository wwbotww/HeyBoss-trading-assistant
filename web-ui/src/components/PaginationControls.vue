<script setup lang="ts">
import { ChevronLeft, ChevronRight } from '@lucide/vue'
import { computed } from 'vue'

const props = defineProps<{
  offset: number
  limit: number
  itemCount: number
  hasMore: boolean
}>()

const emit = defineEmits<{
  change: [offset: number]
}>()

const page = computed(() => Math.floor(props.offset / props.limit) + 1)
const rangeStart = computed(() => (props.itemCount === 0 ? 0 : props.offset + 1))
const rangeEnd = computed(() => props.offset + props.itemCount)
</script>

<template>
  <nav class="pagination" aria-label="列表分页">
    <p class="tabular">第 {{ rangeStart }}–{{ rangeEnd }} 条 · 第 {{ page }} 页</p>
    <div>
      <button
        type="button"
        :disabled="offset === 0"
        aria-label="上一页"
        @click="emit('change', Math.max(0, offset - limit))"
      >
        <ChevronLeft :size="16" aria-hidden="true" />
        上一页
      </button>
      <button
        type="button"
        :disabled="!hasMore"
        aria-label="下一页"
        @click="emit('change', offset + limit)"
      >
        下一页
        <ChevronRight :size="16" aria-hidden="true" />
      </button>
    </div>
  </nav>
</template>

<style scoped>
.pagination {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  padding: 14px 18px;
  color: var(--color-text-faint);
  border-top: 1px solid var(--color-line);
}

.pagination p {
  margin: 0;
  font-size: 0.72rem;
}

.pagination div {
  display: flex;
  gap: 8px;
}

.pagination button {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  min-height: 34px;
  padding: 0 11px;
  color: var(--color-text-soft);
  font-size: 0.72rem;
  font-weight: 650;
  cursor: pointer;
  background: #fff;
  border: 1px solid var(--color-line-strong);
  border-radius: 10px;
}

.pagination button:hover:not(:disabled) {
  color: var(--color-text);
  background: var(--color-surface-soft);
}

.pagination button:disabled {
  cursor: default;
  opacity: 0.38;
}

@media (max-width: 520px) {
  .pagination {
    align-items: stretch;
    flex-direction: column;
  }

  .pagination div {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
  }

  .pagination button {
    justify-content: center;
  }
}
</style>

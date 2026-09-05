<script setup lang="ts">
withDefaults(
  defineProps<{
    caption: string
    minWidth?: string
  }>(),
  {
    minWidth: '760px',
  },
)
</script>

<template>
  <div class="table-scroll" role="region" :aria-label="caption" tabindex="0">
    <table :style="{ minWidth }">
      <caption class="sr-only">
        {{
          caption
        }}
      </caption>
      <slot />
    </table>
  </div>
</template>

<style scoped>
.table-scroll {
  width: 100%;
  overflow-x: auto;
  overscroll-behavior-inline: contain;
}

table {
  width: 100%;
  border-spacing: 0;
  border-collapse: collapse;
}

:deep(th) {
  padding: 11px 18px;
  color: var(--color-text-faint);
  font-size: 0.66rem;
  font-weight: 700;
  text-align: left;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  background: var(--color-surface-soft);
  border-top: 1px solid var(--color-line);
  border-bottom: 1px solid var(--color-line);
}

:deep(td) {
  padding: 16px 18px;
  color: var(--color-text-soft);
  font-size: 0.78rem;
  vertical-align: middle;
  border-bottom: 1px solid var(--color-line);
}

:deep(tbody tr:last-child td) {
  border-bottom: 0;
}

:deep(tbody tr:hover td) {
  background: #fbfbfc;
}

:deep(.cell-primary) {
  color: var(--color-text);
  font-weight: 640;
}

:deep(.cell-stack) {
  display: grid;
  gap: 4px;
}

:deep(.cell-subtle) {
  color: var(--color-text-faint);
  font-size: 0.69rem;
}

:deep(.align-right) {
  text-align: right;
}
</style>

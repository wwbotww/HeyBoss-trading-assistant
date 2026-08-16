<script setup lang="ts">
import { AlertCircle, CircleCheck, Database, LoaderCircle, RefreshCw } from '@lucide/vue'
import { computed, type Component } from 'vue'

import type { SourceState } from '../api/types'

type DisplayState = SourceState | 'loading' | 'error'

const props = withDefaults(
  defineProps<{
    state: DisplayState
    title?: string | undefined
    detail?: string | undefined
    retryLabel?: string | undefined
  }>(),
  {
    title: '',
    detail: '',
    retryLabel: '',
  },
)

const emit = defineEmits<{
  retry: []
}>()

const stateDefaults: Readonly<
  Record<DisplayState, { title: string; detail: string; icon: Component }>
> = {
  available: { title: '数据可用', detail: '数据源响应正常。', icon: CircleCheck },
  empty: { title: '暂无记录', detail: '数据源存在，但当前没有可展示的记录。', icon: Database },
  missing: { title: '数据尚未生成', detail: '本地只读数据源目前不存在。', icon: Database },
  invalid: { title: '数据无法读取', detail: '数据源内容未通过完整性检查。', icon: AlertCircle },
  unconfigured: { title: '尚未配置', detail: '当前环境没有配置该只读数据源。', icon: Database },
  unobserved: { title: '尚未观测', detail: '当前没有足够证据判断数据状态。', icon: Database },
  loading: { title: '正在读取', detail: '正在整理最新的只读交易视图。', icon: LoaderCircle },
  error: { title: '暂时无法读取', detail: 'Web API 未返回可用结果。', icon: AlertCircle },
}

const presentation = computed(() => stateDefaults[props.state])
</script>

<template>
  <div class="data-state" :class="`data-state--${state}`" role="status">
    <span class="state-icon" aria-hidden="true">
      <component :is="presentation.icon" :size="22" :stroke-width="1.8" />
    </span>
    <div>
      <strong>{{ title || presentation.title }}</strong>
      <p>{{ detail || presentation.detail }}</p>
      <button v-if="retryLabel" type="button" @click="emit('retry')">
        <RefreshCw :size="14" aria-hidden="true" />
        {{ retryLabel }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.data-state {
  display: flex;
  align-items: flex-start;
  gap: 14px;
  min-height: 140px;
  padding: 24px;
  color: var(--color-text-soft);
  background: var(--color-surface-soft);
  border: 1px dashed var(--color-line-strong);
  border-radius: var(--radius-md);
}

.state-icon {
  display: grid;
  flex: 0 0 auto;
  width: 40px;
  height: 40px;
  color: var(--color-text);
  background: #fff;
  border: 1px solid var(--color-line);
  border-radius: 13px;
  place-items: center;
}

.data-state--loading .state-icon :deep(svg) {
  animation: state-spin 1.2s linear infinite;
}

.data-state--error .state-icon,
.data-state--invalid .state-icon {
  color: var(--color-negative);
}

.data-state strong {
  display: block;
  margin: 2px 0 6px;
  color: var(--color-text);
  font-size: 0.88rem;
}

.data-state p {
  max-width: 560px;
  margin: 0;
  font-size: 0.78rem;
  line-height: 1.55;
}

.data-state button {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 7px 10px;
  margin-top: 13px;
  font-size: 0.72rem;
  font-weight: 650;
  cursor: pointer;
  background: #fff;
  border: 1px solid var(--color-line-strong);
  border-radius: 9px;
}

@keyframes state-spin {
  to {
    transform: rotate(360deg);
  }
}
</style>

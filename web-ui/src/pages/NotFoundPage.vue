<script setup lang="ts">
import { ArrowLeft } from '@lucide/vue'
import { RouterLink } from 'vue-router'
</script>

<template>
  <section class="not-found surface">
    <span>404</span>
    <h1>这条路径没有对应视图。</h1>
    <p>当前交易助手只开放总览、账户、决策流和订单账本。</p>
    <RouterLink to="/">
      <ArrowLeft :size="16" aria-hidden="true" />
      返回操作总览
    </RouterLink>
  </section>
</template>

<style scoped>
.not-found {
  display: grid;
  min-height: 440px;
  padding: 44px;
  place-content: center;
  justify-items: center;
  text-align: center;
}

.not-found > span {
  color: var(--color-brand-violet);
  font-size: 0.7rem;
  font-weight: 750;
  letter-spacing: 0.14em;
}

.not-found h1 {
  margin: 18px 0 10px;
  font-size: clamp(1.8rem, 4vw, 3rem);
  letter-spacing: -0.05em;
}

.not-found p {
  margin: 0;
  color: var(--color-text-soft);
  font-size: 0.86rem;
}

.not-found a {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 10px 14px;
  margin-top: 28px;
  font-size: 0.75rem;
  font-weight: 650;
  background: var(--color-surface-soft);
  border: 1px solid var(--color-line);
  border-radius: 999px;
}
</style>

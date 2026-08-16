<script setup lang="ts">
import {
  Activity,
  BrainCircuit,
  Database,
  FlaskConical,
  LayoutDashboard,
  ListOrdered,
  Menu,
  RefreshCw,
  ShieldCheck,
  WalletCards,
} from '@lucide/vue'
import { useIsFetching, useQuery, useQueryClient } from '@tanstack/vue-query'
import { computed, ref, type Component, watch } from 'vue'
import { RouterLink, RouterView, useRoute } from 'vue-router'

import { healthQuery } from '../api/queries'
import BrandMark from './BrandMark.vue'
import SideDrawer from './SideDrawer.vue'
import StatusPill from './StatusPill.vue'

interface NavigationItem {
  to: string
  label: string
  shortLabel: string
  icon: Component
  group: 'trade' | 'research'
}

const navigation: readonly NavigationItem[] = [
  { to: '/', label: '操作总览', shortLabel: '总览', icon: LayoutDashboard, group: 'trade' },
  { to: '/portfolio', label: '账户与持仓', shortLabel: '持仓', icon: WalletCards, group: 'trade' },
  { to: '/activity', label: '决策流', shortLabel: '决策', icon: Activity, group: 'trade' },
  { to: '/orders', label: '订单与成交', shortLabel: '订单', icon: ListOrdered, group: 'trade' },
  {
    to: '/strategy',
    label: '策略与因子',
    shortLabel: '策略',
    icon: BrainCircuit,
    group: 'research',
  },
  {
    to: '/backtests',
    label: '回测中心',
    shortLabel: '回测',
    icon: FlaskConical,
    group: 'research',
  },
  { to: '/system', label: '数据与系统', shortLabel: '系统', icon: Database, group: 'research' },
]
const tradingNavigation = navigation.filter((item) => item.group === 'trade')
const researchNavigation = navigation.filter((item) => item.group === 'research')

const route = useRoute()
const queryClient = useQueryClient()
const fetchingCount = useIsFetching()
const health = useQuery(healthQuery())
const mobileMenuOpen = ref(false)

const title = computed(() =>
  typeof route.meta.title === 'string' ? route.meta.title : 'Trading Assistant',
)
const kicker = computed(() => (typeof route.meta.kicker === 'string' ? route.meta.kicker : ''))
const healthLabel = computed(() => {
  if (health.isPending.value) {
    return '连接检查中'
  }
  if (health.isError.value) {
    return 'API 不可用'
  }
  return 'API 正常'
})
const healthStatus = computed(() => (health.isSuccess.value ? 'ok' : 'unobserved'))

watch(
  () => route.fullPath,
  () => {
    mobileMenuOpen.value = false
  },
)

async function refreshAll(): Promise<void> {
  await queryClient.invalidateQueries()
}
</script>

<template>
  <div class="app-shell">
    <aside class="sidebar">
      <BrandMark />

      <nav class="primary-nav" aria-label="主导航">
        <div class="nav-group">
          <p>交易</p>
          <RouterLink
            v-for="item in tradingNavigation"
            :key="item.to"
            :to="item.to"
            exact-active-class="is-active"
          >
            <component :is="item.icon" :size="18" :stroke-width="1.8" aria-hidden="true" />
            <span>{{ item.label }}</span>
          </RouterLink>
        </div>
        <div class="nav-group">
          <p>研究与系统</p>
          <RouterLink
            v-for="item in researchNavigation"
            :key="item.to"
            :to="item.to"
            exact-active-class="is-active"
          >
            <component :is="item.icon" :size="18" :stroke-width="1.8" aria-hidden="true" />
            <span>{{ item.label }}</span>
          </RouterLink>
        </div>
      </nav>

      <div class="sidebar-context">
        <div class="context-icon" aria-hidden="true">
          <ShieldCheck :size="18" :stroke-width="1.8" />
        </div>
        <div>
          <strong>Paper · 只读</strong>
          <span>所有操作均经过既有交易链路</span>
        </div>
      </div>
    </aside>

    <div class="workspace">
      <header class="topbar">
        <div class="topbar-leading">
          <button
            class="menu-button"
            type="button"
            aria-label="打开主导航"
            @click="mobileMenuOpen = true"
          >
            <Menu :size="19" aria-hidden="true" />
          </button>
          <div class="topbar-title">
            <p>{{ kicker }}</p>
            <strong>{{ title }}</strong>
          </div>
        </div>
        <div class="topbar-actions">
          <StatusPill :status="healthStatus" :label="healthLabel" />
          <button
            class="refresh-button"
            type="button"
            :disabled="fetchingCount > 0"
            aria-label="刷新全部只读数据"
            title="刷新全部只读数据"
            @click="refreshAll"
          >
            <RefreshCw
              :size="17"
              :stroke-width="1.9"
              aria-hidden="true"
              :class="{ spinning: fetchingCount > 0 }"
            />
          </button>
        </div>
      </header>

      <main class="content">
        <RouterView />
      </main>
    </div>

    <SideDrawer
      :open="mobileMenuOpen"
      title="HeyBoss 导航"
      description="交易、研究和系统状态均来自同一套只读 Web API。"
      width="380px"
      @close="mobileMenuOpen = false"
    >
      <nav class="drawer-navigation" aria-label="移动端主导航">
        <p>交易</p>
        <RouterLink
          v-for="item in tradingNavigation"
          :key="item.to"
          :to="item.to"
          exact-active-class="is-active"
        >
          <component :is="item.icon" :size="19" :stroke-width="1.8" aria-hidden="true" />
          <span
            ><strong>{{ item.label }}</strong
            ><small>{{ item.shortLabel }}</small></span
          >
        </RouterLink>
        <p>研究与系统</p>
        <RouterLink
          v-for="item in researchNavigation"
          :key="item.to"
          :to="item.to"
          exact-active-class="is-active"
        >
          <component :is="item.icon" :size="19" :stroke-width="1.8" aria-hidden="true" />
          <span
            ><strong>{{ item.label }}</strong
            ><small>{{ item.shortLabel }}</small></span
          >
        </RouterLink>
      </nav>
      <div class="drawer-boundary">
        <ShieldCheck :size="18" aria-hidden="true" />
        <div><strong>Paper · 只读</strong><span>页面不形成第二条执行路径</span></div>
      </div>
    </SideDrawer>
  </div>
</template>

<style scoped>
.app-shell {
  display: grid;
  min-height: 100vh;
  grid-template-columns: 236px minmax(0, 1fr);
}

.sidebar {
  position: sticky;
  top: 0;
  display: flex;
  flex-direction: column;
  height: 100vh;
  padding: 24px 20px;
  background: rgb(255 255 255 / 92%);
  border-right: 1px solid var(--color-line);
  backdrop-filter: blur(18px);
}

.primary-nav {
  display: grid;
  gap: 22px;
  margin-top: 38px;
}

.nav-group {
  display: grid;
  gap: 6px;
}

.nav-group > p {
  padding: 0 13px;
  margin: 0 0 3px;
  color: var(--color-text-faint);
  font-size: 0.58rem;
  font-weight: 740;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}

.primary-nav a {
  position: relative;
  display: flex;
  align-items: center;
  gap: 12px;
  min-height: 44px;
  padding: 0 13px;
  color: var(--color-text-soft);
  font-size: 0.79rem;
  font-weight: 580;
  border-radius: 13px;
  transition:
    color 160ms ease,
    background 160ms ease;
}

.primary-nav a:hover {
  color: var(--color-text);
  background: var(--color-surface-soft);
}

.primary-nav a.is-active {
  color: var(--color-text);
  background: #f0f1f4;
}

.primary-nav a.is-active::before {
  position: absolute;
  left: 0;
  width: 3px;
  height: 19px;
  content: '';
  background: var(--gradient-brand);
  border-radius: 0 4px 4px 0;
}

.sidebar-context {
  display: flex;
  align-items: flex-start;
  gap: 11px;
  padding: 14px;
  margin-top: auto;
  background: var(--color-surface-soft);
  border: 1px solid var(--color-line);
  border-radius: var(--radius-md);
}

.context-icon {
  display: grid;
  flex: 0 0 auto;
  width: 32px;
  height: 32px;
  color: var(--color-positive);
  background: var(--color-positive-bg);
  border-radius: 10px;
  place-items: center;
}

.sidebar-context div:last-child {
  display: grid;
  gap: 5px;
}

.sidebar-context strong {
  font-size: 0.72rem;
}

.sidebar-context span {
  color: var(--color-text-faint);
  font-size: 0.62rem;
  line-height: 1.45;
}

.workspace {
  min-width: 0;
}

.topbar {
  position: sticky;
  top: 0;
  z-index: 10;
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 76px;
  padding: 0 34px;
  background: rgb(244 245 247 / 86%);
  border-bottom: 1px solid rgb(230 231 235 / 76%);
  backdrop-filter: blur(20px);
}

.topbar-title {
  display: grid;
  gap: 3px;
}

.topbar-leading {
  display: flex;
  align-items: center;
  gap: 12px;
}

.menu-button {
  display: none;
  width: 36px;
  height: 36px;
  color: var(--color-text-soft);
  cursor: pointer;
  background: #fff;
  border: 1px solid var(--color-line);
  border-radius: 11px;
  place-items: center;
}

.topbar-title p {
  margin: 0;
  color: var(--color-text-faint);
  font-size: 0.59rem;
  font-weight: 720;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}

.topbar-title strong {
  font-size: 0.87rem;
}

.topbar-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.refresh-button {
  display: grid;
  width: 36px;
  height: 36px;
  color: var(--color-text-soft);
  cursor: pointer;
  background: #fff;
  border: 1px solid var(--color-line);
  border-radius: 50%;
  place-items: center;
}

.refresh-button:hover:not(:disabled) {
  color: var(--color-text);
  border-color: var(--color-line-strong);
}

.refresh-button:disabled {
  cursor: progress;
  opacity: 0.7;
}

.spinning {
  animation: refresh-spin 900ms linear infinite;
}

.content {
  width: min(100%, calc(var(--content-max) + 68px));
  padding: 32px 34px 64px;
  margin: 0 auto;
}

.drawer-navigation {
  display: grid;
  gap: 7px;
}

.drawer-navigation > p {
  margin: 14px 11px 3px;
  color: var(--color-text-faint);
  font-size: 0.61rem;
  font-weight: 720;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}

.drawer-navigation > p:first-child {
  margin-top: 0;
}

.drawer-navigation a {
  display: flex;
  align-items: center;
  gap: 13px;
  min-height: 54px;
  padding: 9px 12px;
  color: var(--color-text-soft);
  border-radius: 14px;
}

.drawer-navigation a.is-active {
  color: var(--color-text);
  background: var(--color-surface-soft);
}

.drawer-navigation a > span {
  display: grid;
  gap: 3px;
}

.drawer-navigation strong {
  font-size: 0.78rem;
}

.drawer-navigation small {
  color: var(--color-text-faint);
  font-size: 0.62rem;
}

.drawer-boundary {
  display: flex;
  align-items: center;
  gap: 11px;
  padding: 14px;
  margin-top: 24px;
  color: var(--color-positive);
  background: var(--color-positive-bg);
  border-radius: var(--radius-md);
}

.drawer-boundary div {
  display: grid;
  gap: 3px;
}

.drawer-boundary strong {
  color: var(--color-text);
  font-size: 0.72rem;
}

.drawer-boundary span {
  color: var(--color-text-soft);
  font-size: 0.63rem;
}

@keyframes refresh-spin {
  to {
    transform: rotate(360deg);
  }
}

@media (max-width: 920px) {
  .app-shell {
    display: block;
  }

  .sidebar {
    display: none;
  }

  .topbar {
    padding: 0 20px;
  }

  .menu-button {
    display: grid;
  }

  .content {
    padding: 26px 20px 56px;
  }
}

@media (max-width: 520px) {
  .topbar-actions :deep(.status-pill) {
    display: none;
  }

  .content {
    padding: 22px 14px 48px;
  }
}
</style>

import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  scrollBehavior: () => ({ top: 0 }),
  routes: [
    {
      path: '/',
      name: 'overview',
      component: () => import('./pages/OverviewPage.vue'),
      meta: { title: '操作总览', kicker: 'Today' },
    },
    {
      path: '/portfolio',
      name: 'portfolio',
      component: () => import('./pages/PortfolioPage.vue'),
      meta: { title: '账户与持仓', kicker: 'Portfolio' },
    },
    {
      path: '/strategy',
      name: 'strategy',
      component: () => import('./pages/StrategyPage.vue'),
      meta: { title: '策略与因子', kicker: 'Strategy intelligence' },
    },
    {
      path: '/activity',
      name: 'activity',
      component: () => import('./pages/ActivityPage.vue'),
      meta: { title: '决策流', kicker: 'Decision flow' },
    },
    {
      path: '/orders',
      name: 'orders',
      component: () => import('./pages/OrdersPage.vue'),
      meta: { title: '订单与成交', kicker: 'Execution ledger' },
    },
    {
      path: '/market-radar',
      name: 'market-radar',
      component: () => import('./pages/MarketRadarPage.vue'),
      meta: { title: '市场雷达', kicker: 'Market intelligence' },
    },
    {
      path: '/backtests',
      name: 'backtests',
      component: () => import('./pages/BacktestsPage.vue'),
      meta: { title: '回测中心', kicker: 'Research ledger' },
    },
    {
      path: '/system',
      name: 'system',
      component: () => import('./pages/SystemPage.vue'),
      meta: { title: '数据与系统', kicker: 'Observability' },
    },
    {
      path: '/:pathMatch(.*)*',
      name: 'not-found',
      component: () => import('./pages/NotFoundPage.vue'),
      meta: { title: '页面不存在', kicker: '404' },
    },
  ],
})

router.afterEach((to) => {
  const title = typeof to.meta.title === 'string' ? to.meta.title : 'Trading Assistant'
  document.title = `${title} · HeyBoss`
})

export default router

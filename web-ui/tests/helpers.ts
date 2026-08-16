import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { mount, type VueWrapper } from '@vue/test-utils'
import type { Component } from 'vue'
import { createMemoryHistory, createRouter, type Router, type RouteRecordRaw } from 'vue-router'

interface MountedPage {
  wrapper: VueWrapper
  router: Router
  queryClient: QueryClient
}

const EmptyRoute = { template: '<div />' }
const routes: readonly RouteRecordRaw[] = [
  { path: '/', component: EmptyRoute },
  { path: '/portfolio', component: EmptyRoute },
  { path: '/activity', component: EmptyRoute },
  { path: '/orders', component: EmptyRoute },
  { path: '/strategy', component: EmptyRoute },
  { path: '/backtests', component: EmptyRoute },
  { path: '/system', component: EmptyRoute },
]

export async function mountPage(component: Component, initialPath = '/'): Promise<MountedPage> {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [...routes],
  })
  await router.push(initialPath)
  await router.isReady()

  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        gcTime: Number.POSITIVE_INFINITY,
      },
    },
  })
  const wrapper = mount(component, {
    attachTo: document.body,
    global: {
      plugins: [router, [VueQueryPlugin, { queryClient }]],
      stubs: { Teleport: true },
    },
  })
  return { wrapper, router, queryClient }
}

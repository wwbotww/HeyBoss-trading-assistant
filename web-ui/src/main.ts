import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { createApp } from 'vue'

import App from './App.vue'
import router from './router'
import './styles/tokens.css'
import './styles/base.css'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 60_000,
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
})

// .vue 默认导出的组件类型由 vue-tsc 校验。
// eslint-disable-next-line @typescript-eslint/no-unsafe-argument
createApp(App).use(router).use(VueQueryPlugin, { queryClient }).mount('#app')

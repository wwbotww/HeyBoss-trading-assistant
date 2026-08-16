import { flushPromises } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import ActivityPage from '../src/pages/ActivityPage.vue'
import { installApiMock } from './fixtures'
import { mountPage } from './helpers'

describe('决策流', () => {
  it('在工作流和逐标的信号之间保留 URL 状态', async () => {
    installApiMock()
    const { wrapper, router } = await mountPage(ActivityPage, '/activity')
    await flushPromises()

    expect(wrapper.text()).toContain('待审批')
    expect(wrapper.text()).toContain('All application risk checks passed')

    await wrapper.get('.row-trigger').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query.event).toBe('evt-20260815-001')
    expect(wrapper.text()).toContain('审计时间线')
    expect(wrapper.text()).toContain('PatchTST factor rank')

    await wrapper.get('button[aria-label="关闭详情"]').trigger('click')
    await flushPromises()

    const signalsTab = wrapper
      .findAll('button')
      .find((button) => button.text().includes('逐标的信号'))
    if (!signalsTab) {
      throw new Error('逐标的信号标签页不存在')
    }
    await signalsTab.trigger('click')
    await flushPromises()

    expect(router.currentRoute.value.query.tab).toBe('signals')
    expect(wrapper.text()).toContain('AAPL.US')
    expect(wrapper.text()).toContain('33.33%')
    expect(wrapper.text()).toContain('策略输出')
  })

  it('把信号筛选同步到 URL 和服务端查询', async () => {
    const fetchMock = installApiMock()
    const { wrapper, router } = await mountPage(
      ActivityPage,
      '/activity?tab=signals&status=NEW&instrument=AAPL.US',
    )
    await flushPromises()

    expect(router.currentRoute.value.query.status).toBe('NEW')
    const signalRequest = fetchMock.mock.calls
      .map((call) => call[0])
      .find(
        (request) => request instanceof Request && new URL(request.url).pathname === '/api/signals',
      )
    expect(signalRequest).toBeInstanceOf(Request)
    if (signalRequest instanceof Request) {
      expect(new URL(signalRequest.url).searchParams.get('instrument_id')).toBe('AAPL.US')
    }

    await wrapper.get('button.clear-filter').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query.status).toBeUndefined()
    expect(router.currentRoute.value.query.instrument).toBeUndefined()
  })
})

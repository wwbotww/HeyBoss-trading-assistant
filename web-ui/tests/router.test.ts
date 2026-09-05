import { describe, expect, it } from 'vitest'
import type { RouteLocationNormalized } from 'vue-router'

import router from '../src/router'

function location(path: string): RouteLocationNormalized {
  const resolved = router.resolve(path)
  return { ...resolved, name: resolved.name ?? undefined }
}

describe('生产路由滚动规则', () => {
  const paths = [
    '/',
    '/portfolio',
    '/strategy',
    '/activity',
    '/orders',
    '/market-radar',
    '/backtests',
    '/system',
  ]

  it.each(paths)('%s 的同路径查询不主动滚动，跨页面回顶', async (path) => {
    const scroll = router.options.scrollBehavior
    if (!scroll) throw new Error('生产路由缺少滚动规则')
    const current = location(path)
    expect(await scroll(location(`${path}?page=2`), current, null)).toBe(false)
    expect(await scroll(current, location(path === '/' ? '/system' : '/'), null)).toEqual({
      top: 0,
    })
    const saved = { left: 12, top: 1280 }
    expect(await scroll(current, location('/orders?tab=fills'), saved)).toEqual(saved)
    expect(await scroll(location(`${path}?page=3`), current, saved)).toEqual(saved)
  })

  it.each([
    '?event_kind=economic&event_date=2026-09-07',
    '?view=sectors&sector=information_technology',
    '?view=stocks&dimension=fundamentals&instrument=AAPL.US',
    '?view=stocks&query=AAPL&sort=momentum&direction=desc&page=2',
  ])('市场雷达 %s 不另设滚动特例', async (query) => {
    expect(
      await router.options.scrollBehavior?.(
        location(`/market-radar${query}`),
        location('/market-radar'),
        null,
      ),
    ).toBe(false)
  })
})

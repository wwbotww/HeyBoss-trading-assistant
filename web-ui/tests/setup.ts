import { afterEach, vi } from 'vitest'

vi.mock('echarts/core', () => ({
  use: vi.fn(),
  init: vi.fn(() => ({
    setOption: vi.fn(),
    resize: vi.fn(),
    clear: vi.fn(),
    dispose: vi.fn(),
  })),
}))

afterEach(() => {
  vi.unstubAllGlobals()
  document.body.innerHTML = ''
})

import type { LocationQuery, LocationQueryRaw } from 'vue-router'

export const PAGE_SIZE = 20
export const CHART_WINDOW_SIZE = 100
export const REPORT_PAGE_SIZE = 50

export function readQueryText(value: unknown): string {
  if (typeof value === 'string') {
    return value.trim()
  }
  if (Array.isArray(value) && typeof value[0] === 'string') {
    return value[0].trim()
  }
  return ''
}

export function readPage(value: unknown): number {
  const text = readQueryText(value)
  if (!/^\d+$/.test(text)) {
    return 1
  }
  const parsed = Number(text)
  return Number.isSafeInteger(parsed) && parsed >= 1 ? parsed : 1
}

export function pageOffset(page: number, limit = PAGE_SIZE): number {
  return Math.max(0, page - 1) * limit
}

export function withPage(query: LocationQuery, page: number): LocationQueryRaw {
  const next: LocationQueryRaw = { ...query }
  if (page <= 1) {
    delete next.page
  } else {
    next.page = String(page)
  }
  return next
}

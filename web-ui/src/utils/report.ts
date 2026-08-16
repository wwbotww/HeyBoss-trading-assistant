import type { ReportTable } from '../api/types'
import {
  formatCurrency,
  formatDateTime,
  formatNumber,
  formatPercent,
  formatQuantity,
} from './format'

export interface EquitySeriesResult {
  points: { label: string; value: number }[]
  error: string | null
}

export function extractEquitySeries(table: ReportTable): EquitySeriesResult {
  if (!table.columns.includes('timestamp_utc') || !table.columns.includes('equity')) {
    return { points: [], error: '权益报告缺少 timestamp_utc 或 equity 列。' }
  }
  const points: { label: string; value: number }[] = []
  for (const [index, row] of table.rows.entries()) {
    const timestamp = row.timestamp_utc
    const equity = row.equity
    if (
      typeof timestamp !== 'string' ||
      Number.isNaN(new Date(timestamp).getTime()) ||
      typeof equity !== 'number' ||
      !Number.isFinite(equity)
    ) {
      return { points: [], error: `权益报告第 ${String(index + 1)} 行字段无效。` }
    }
    points.push({ label: timestamp.slice(0, 10), value: equity })
  }
  return { points, error: null }
}

export function formatReportValue(value: string | number | boolean | null, column: string): string {
  if (value === null) {
    return '—'
  }
  const normalized = column.toLowerCase()
  if (typeof value === 'boolean') {
    return value ? '是' : '否'
  }
  if (typeof value === 'number') {
    if (normalized.includes('quantity')) {
      return formatQuantity(value)
    }
    if (
      normalized.includes('equity') ||
      normalized.includes('cash') ||
      normalized.includes('price') ||
      normalized.includes('commission') ||
      normalized.includes('market_value') ||
      normalized.includes('notional')
    ) {
      return formatCurrency(value, 'USD')
    }
    if (normalized.includes('return') || normalized.includes('drawdown')) {
      return formatPercent(value)
    }
    return formatNumber(value, 4)
  }
  if (normalized.includes('timestamp') || normalized.endsWith('_at_utc')) {
    return formatDateTime(value)
  }
  return value || '—'
}

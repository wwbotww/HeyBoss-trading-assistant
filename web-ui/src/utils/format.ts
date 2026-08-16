const EMPTY_VALUE = '—'

const statusLabels: Readonly<Record<string, string>> = {
  ACCEPTED: '已接受',
  APPROVED: '已批准',
  CANCELLED: '已取消',
  DENIED: '已否决',
  EXPIRED: '已过期',
  FAILED: '失败',
  FILLED: '已成交',
  NEW: '待处理',
  PARTIALLY_FILLED: '部分成交',
  PENDING_APPROVAL: '待审批',
  PLANNED: '已规划',
  REJECTED: '已拒绝',
  SUBMITTED: '已提交',
}

const sourceLabels: Readonly<Record<string, string>> = {
  available: '数据可用',
  empty: '暂无数据',
  invalid: '数据异常',
  missing: '数据缺失',
  unconfigured: '尚未配置',
  unobserved: '尚未观测',
}

export function formatCurrency(
  value: number | null | undefined,
  currency: string | null | undefined = 'USD',
): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return EMPTY_VALUE
  }
  const normalizedCurrency = currency?.match(/^[A-Z]{3}$/) ? currency : 'USD'
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: normalizedCurrency,
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value)
}

export function formatNumber(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return EMPTY_VALUE
  }
  return new Intl.NumberFormat('en-US', {
    minimumFractionDigits: 0,
    maximumFractionDigits: digits,
  }).format(value)
}

export function formatQuantity(value: number | null | undefined): string {
  return formatNumber(value, 4)
}

export function formatPercent(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return EMPTY_VALUE
  }
  return new Intl.NumberFormat('zh-CN', {
    style: 'percent',
    minimumFractionDigits: 1,
    maximumFractionDigits: 2,
  }).format(value)
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) {
    return EMPTY_VALUE
  }
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return EMPTY_VALUE
  }
  const formatted = new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'UTC',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(date)
  return `${formatted} UTC`
}

export function formatAge(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds) || seconds < 0) {
    return EMPTY_VALUE
  }
  if (seconds < 60) {
    return `${String(Math.round(seconds))} 秒前`
  }
  if (seconds < 3600) {
    return `${String(Math.round(seconds / 60))} 分钟前`
  }
  if (seconds < 86_400) {
    return `${String(Math.round(seconds / 3600))} 小时前`
  }
  return `${String(Math.round(seconds / 86_400))} 天前`
}

export function formatStatus(status: string): string {
  return statusLabels[status.toUpperCase()] ?? status
}

export function formatSourceState(state: string): string {
  return sourceLabels[state] ?? state
}

export function formatDirection(direction: string): string {
  const normalized = direction.toUpperCase()
  if (normalized === 'BUY' || normalized === 'LONG') {
    return '买入'
  }
  if (normalized === 'SELL' || normalized === 'SHORT') {
    return '卖出'
  }
  return direction
}

export function formatStrategyName(name: string | null | undefined): string {
  if (!name) {
    return EMPTY_VALUE
  }
  if (name.toLowerCase() === 'patchtst_e3') {
    return 'PatchTST E3'
  }
  return name.replaceAll('_', ' ')
}

export function formatLabel(value: string): string {
  return value.replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase())
}

export function formatScalar(value: string | number | boolean | null): string {
  if (value === null || value === '') {
    return EMPTY_VALUE
  }
  if (typeof value === 'boolean') {
    return value ? '是' : '否'
  }
  if (typeof value === 'number') {
    return formatNumber(value, 6)
  }
  return value
}

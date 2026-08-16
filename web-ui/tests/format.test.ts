import { describe, expect, it } from 'vitest'

import {
  formatAge,
  formatCurrency,
  formatDateTime,
  formatDirection,
  formatLabel,
  formatPercent,
  formatScalar,
  formatSourceState,
  formatStatus,
  formatStrategyName,
} from '../src/utils/format'

describe('展示格式', () => {
  it('统一处理金额、比例和空值', () => {
    expect(formatCurrency(12_345.6, 'USD')).toBe('$12,345.60')
    expect(formatCurrency(null, 'USD')).toBe('—')
    expect(formatPercent(0.125)).toBe('12.5%')
  })

  it('始终以 UTC 展示后端时间', () => {
    expect(formatDateTime('2026-08-15T12:34:56Z')).toContain('UTC')
    expect(formatDateTime('not-a-date')).toBe('—')
  })

  it('把业务枚举转换为明确中文', () => {
    expect(formatStatus('PENDING_APPROVAL')).toBe('待审批')
    expect(formatSourceState('invalid')).toBe('数据异常')
    expect(formatDirection('BUY')).toBe('买入')
    expect(formatStrategyName('patchtst_e3')).toBe('PatchTST E3')
    expect(formatAge(7_200)).toBe('2 小时前')
    expect(formatLabel('max_drawdown')).toBe('Max Drawdown')
    expect(formatScalar(true)).toBe('是')
  })
})

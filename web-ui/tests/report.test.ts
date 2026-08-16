import { describe, expect, it } from 'vitest'

import { extractEquitySeries, formatReportValue } from '../src/utils/report'
import { equityReportFixture } from './fixtures'

describe('回测报告适配', () => {
  it('只从确定列提取权益数据', () => {
    expect(extractEquitySeries(equityReportFixture)).toEqual({
      points: [
        { label: '2026-01-01', value: 100_000 },
        { label: '2026-01-02', value: 105_000 },
      ],
      error: null,
    })
    expect(
      extractEquitySeries({ ...equityReportFixture, columns: ['date', 'equity'] }).error,
    ).toContain('timestamp_utc')
  })

  it('按报告字段语义格式化标量', () => {
    expect(formatReportValue(100_000, 'equity')).toBe('$100,000.00')
    expect(formatReportValue(0.05, 'daily_return')).toBe('5.0%')
    expect(formatReportValue(true, 'active')).toBe('是')
  })
})

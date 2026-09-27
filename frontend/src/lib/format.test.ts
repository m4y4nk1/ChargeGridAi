import { describe, expect, it } from 'vitest'
import { formatCount, formatCrore, formatDate, formatLakh, formatPercent } from './format'

describe('format', () => {
  it('formats dates as DD MMM YYYY', () => {
    expect(formatDate('2026-09-04T10:00:00Z')).toBe('04 Sep 2026')
    expect(formatDate(null)).toBe('—')
    expect(formatDate('not a date')).toBe('—')
  })

  it('uses Indian digit grouping', () => {
    expect(formatCount(1234567)).toBe('12,34,567')
    expect(formatCount(undefined)).toBe('—')
  })
})

describe('rupee and percent formatting', () => {
  it('uses crore and lakh with Indian grouping', () => {
    expect(formatCrore(99_506_000)).toBe('₹9.95 Cr')
    expect(formatCrore(1_234_500_000)).toBe('₹123.45 Cr')
    expect(formatLakh(4_114_000)).toBe('₹41.1 L')
    expect(formatCrore(null)).toBe('—')
    // The sign goes before the currency symbol, with a true minus.
    expect(formatCrore(-16_200_000)).toBe('−₹1.62 Cr')
    expect(formatLakh(-490_000)).toBe('−₹4.9 L')
  })

  it('formats shares as percentages', () => {
    expect(formatPercent(0.1138)).toBe('11.4%')
    expect(formatPercent(0.0001, 2)).toBe('0.01%')
    expect(formatPercent(null)).toBe('—')
  })
})

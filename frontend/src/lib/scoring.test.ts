import { describe, expect, it } from 'vitest'
import type { SubScores } from './api'
import { contributions, direction, robustnessBadge } from './scoring'

const W: SubScores = {
  demand: 0.25,
  accessibility: 0.15,
  traffic: 0.15,
  grid: 0.15,
  land: 0.1,
  future_growth: 0.1,
  competition_gap: 0.1,
  equity: 0,
}
const S: SubScores = {
  demand: 100,
  accessibility: 80,
  traffic: 60,
  grid: 40,
  land: 20,
  future_growth: 0,
  competition_gap: 50,
  equity: 50,
}

describe('contributions', () => {
  it('weights each sub-score and sums to the total, skipping zero weights', () => {
    const parts = contributions(S, W)
    expect(parts.map((p) => p.key)).not.toContain('equity')
    expect(parts.find((p) => p.key === 'demand')?.points).toBe(25)
    const total = parts.reduce((a, p) => a + p.points, 0)
    expect(total).toBeCloseTo(25 + 12 + 9 + 6 + 2 + 0 + 5)
  })
})

describe('direction', () => {
  it('compares against the benchmark with a ±10% level band', () => {
    expect(direction(150, 100)).toBe('above')
    expect(direction(105, 100)).toBe('near')
    expect(direction(80, 100)).toBe('below')
    expect(direction(null, 100)).toBeNull()
    expect(direction(Number.NaN, 100)).toBeNull()
  })
})

describe('robustnessBadge', () => {
  it('grades how often a site stays in the top N', () => {
    expect(robustnessBadge(0.93, 20)).toEqual({
      pct: 93,
      tone: 'robust',
      text: 'Top 20 in 93% of weight variations',
    })
    expect(robustnessBadge(0.5, 20)?.tone).toBe('sensitive')
    expect(robustnessBadge(0.1, 20)?.tone).toBe('fragile')
    expect(robustnessBadge(null, 20)).toBeNull()
  })
})

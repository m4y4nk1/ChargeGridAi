import { describe, expect, it } from 'vitest'
import type { OptimisationSummary } from './api'
import { identicalPlans } from './planning'

const opt = (label: string, cost: number, sites: string[]) =>
  ({
    label,
    kpis: {
      cost_inr: cost,
      n_sites: sites.length,
      top_sites: sites.map((site_id) => ({ site_id })),
    },
  }) as unknown as OptimisationSummary

describe('identicalPlans', () => {
  it('groups strategies that picked the same sites at the same cost', () => {
    const groups = identicalPlans([
      opt('Maximum coverage', 10, ['a', 'b']),
      opt('Equity focus', 10, ['a', 'c']),
      opt('Commercial return', 10, ['a', 'b']),
    ])
    expect(groups).toEqual([['Maximum coverage', 'Commercial return']])
  })
})

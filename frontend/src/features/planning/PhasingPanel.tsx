/**
 * Model C phasing view (Section 9.7): which sites to build in 2026, 2028 and 2030 with a
 * budget per period. Sites stay open and may be expanded; solved period by period.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { getJson, postJson } from '../../lib/api'
import { formatCount, formatCrore, formatPercent } from '../../lib/format'

interface PeriodKpi {
  year: number
  budget_inr: number
  spent_inr: number
  new_sites: number
  expanded_sites: number
  n_sites: number
  n_charge_points: number
  coverage_kwh: number
  demand_kwh: number
  coverage_share: number
}

interface Phased {
  id: string
  params: { label: string; method: string }
  kpis: { periods: PeriodKpi[]; discounted_coverage_kwh: number }
  sites?: {
    site_id: string
    name: string | null
    host_type: string
    phase_year: number
    bundle_by_year: Record<string, string>
  }[]
}

const YEARS = [2026, 2028, 2030]

export function PhasingPanel({
  runId,
  baseBudget,
  onSite,
}: {
  runId: string
  baseBudget: number
  onSite: (id: string) => void
}) {
  const qc = useQueryClient()
  const [budgets, setBudgets] = useState(() =>
    YEARS.map(() => String(Math.round(baseBudget / YEARS.length / 1e5) / 100)),
  )
  const existing = useQuery({
    queryKey: ['phased', runId],
    queryFn: async () => {
      const list = await getJson<{ id: string }[]>(`/runs/${runId}/optimisations?kind=phased`)
      const last = list.at(-1)
      return last ? getJson<Phased>(`/optimisations/${last.id}`) : null
    },
  })
  const run = useMutation({
    mutationFn: () =>
      postJson<Phased>(`/runs/${runId}/phasing`, {
        periods: YEARS.map((year, i) => ({
          year,
          budget_inr: Math.round(Number(budgets[i]) * 1e7),
        })),
      }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['phased', runId] }),
  })
  const d = existing.data
  return (
    <section aria-label="Phasing" className="flex flex-col gap-3 text-sm">
      <p className="text-xs text-muted">
        When to build: each period spends its own budget on new sites or on expanding earlier ones.
        Solved period by period, so an early period doesn't hold money back for a later, larger gap.
      </p>
      <form
        className="flex flex-wrap items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault()
          run.mutate()
        }}
      >
        {YEARS.map((y, i) => (
          <label key={y} className="flex flex-col text-xs">
            {y} budget (₹ Cr)
            <input
              value={budgets[i]}
              onChange={(e) => setBudgets((b) => b.map((v, j) => (j === i ? e.target.value : v)))}
              inputMode="decimal"
              className="w-20 rounded border border-line bg-panel px-1 py-0.5"
            />
          </label>
        ))}
        <button
          type="submit"
          disabled={run.isPending}
          className="rounded bg-ink px-3 py-1 text-paper disabled:opacity-50"
        >
          {run.isPending ? 'Phasing… (up to a minute)' : 'Phase the plan'}
        </button>
      </form>
      {run.error && (
        <p role="alert" className="text-warn">
          {run.error.message}
        </p>
      )}
      {d && (
        <>
          <table
            className="w-full text-xs tabular-nums [&_td]:py-0.5 [&_td]:pr-2 [&_th]:pr-2"
            aria-label="Periods"
          >
            <thead>
              <tr className="text-left text-muted">
                <th>Year</th>
                <th>Spent / budget</th>
                <th>New</th>
                <th>Expanded</th>
                <th>Sites</th>
                <th>Served</th>
              </tr>
            </thead>
            <tbody>
              {d.kpis.periods.map((p) => (
                <tr key={p.year} className="border-t border-line">
                  <td>{p.year}</td>
                  <td className="whitespace-nowrap">
                    {formatCrore(p.spent_inr)} / {formatCrore(p.budget_inr)}
                  </td>
                  <td>{p.new_sites}</td>
                  <td>{p.expanded_sites}</td>
                  <td>{p.n_sites}</td>
                  <td className="whitespace-nowrap">
                    {formatCount(p.coverage_kwh)} kWh/d ({formatPercent(p.coverage_share)})
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {YEARS.map((y) => {
            const opened = (d.sites ?? []).filter((s) => s.phase_year === y)
            return (
              <div key={y}>
                <h3 className="text-xs font-semibold">
                  Built in {y} · {opened.length} site{opened.length === 1 ? '' : 's'}
                </h3>
                <ul className="text-xs">
                  {opened.map((s) => (
                    <li key={s.site_id}>
                      <button type="button" className="underline" onClick={() => onSite(s.site_id)}>
                        {s.name ?? 'Unnamed'}
                      </button>{' '}
                      <span className="text-muted">
                        {s.host_type.replaceAll('_', ' ').toLowerCase()} ·{' '}
                        {Object.entries(s.bundle_by_year)
                          .map(([yr, b]) => `${yr}: ${b}`)
                          .join(' → ')}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            )
          })}
          <p className="text-[11px] text-muted">
            {d.params.method}. Candidate sites, require field verification; costs are illustrative
            placeholders.
          </p>
        </>
      )}
    </section>
  )
}

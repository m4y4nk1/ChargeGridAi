/** Per-region data readiness (Phase 12): what each registered region has and lacks. */
import { useQuery } from '@tanstack/react-query'
import { getJson } from '../../lib/api'

interface Dimension {
  score: number
  weight: number
  detail: string
  fix: string
}

interface Readiness {
  region_id: string
  name: string
  status: 'onboarded' | 'planned'
  score: number
  grade: string
  can_plan: boolean
  blockers: string[]
  dimensions: Record<string, Dimension>
}

const LABELS: Record<string, string> = {
  roads_pois: 'Roads & POIs',
  boundaries: 'Boundaries',
  routing: 'Routing graph',
  population: 'Population',
  registrations: 'Vehicle registrations',
  demand: 'Demand runs',
  chargers: 'Existing chargers',
  grid: 'Grid data',
  solar: 'Solar resource',
  tariffs_costs: 'Tariffs & costs',
  operator_feeds: 'Operator feeds',
}

export function RegionReadiness() {
  const q = useQuery({ queryKey: ['readiness'], queryFn: () => getJson<Readiness[]>('/readiness') })
  const rows = Array.isArray(q.data)
    ? q.data.filter((r) => r && typeof r.dimensions === 'object')
    : []
  if (rows.length === 0) return null
  return (
    <section aria-label="Region readiness" className="mt-6">
      <h2 className="text-base font-semibold">Region readiness</h2>
      <p className="mt-1 text-sm text-muted">
        How ready each registered region is for planning, scored from the data actually loaded.
        Planned regions need onboarding (docs/onboarding-regions.md).
      </p>
      <table className="mt-3 w-full text-sm">
        <thead>
          <tr className="text-left text-xs uppercase tracking-wide text-muted">
            <th className="py-1 pr-3">Region</th>
            <th className="py-1 pr-3">Status</th>
            <th className="py-1 pr-3">Score</th>
            <th className="py-1">Missing or weakest</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const weakest = Object.entries(r.dimensions)
              .filter(([, d]) => d.score < 1)
              .sort(
                (a, b) =>
                  a[1].score * a[1].weight - b[1].score * b[1].weight || b[1].weight - a[1].weight,
              )
              .slice(0, 3)
            return (
              <tr key={r.region_id} className="border-t border-line align-top">
                <td className="py-1.5 pr-3">
                  <details>
                    <summary className="cursor-pointer">{r.name}</summary>
                    <ul className="mt-1 text-xs">
                      {Object.entries(r.dimensions).map(([k, d]) => (
                        <li key={k} className="flex gap-2">
                          <span className="w-8 tabular-nums">{Math.round(d.score * 100)}</span>
                          <span className="w-40">{LABELS[k] ?? k}</span>
                          <span className="text-muted">{d.detail}</span>
                        </li>
                      ))}
                    </ul>
                  </details>
                </td>
                <td className="py-1.5 pr-3 text-xs">{r.can_plan ? 'ready to plan' : r.status}</td>
                <td className="py-1.5 pr-3">
                  <div className="flex items-center gap-2">
                    <div className="h-2 w-24 rounded bg-line" aria-hidden>
                      <div className="h-2 rounded bg-ink" style={{ width: `${r.score}%` }} />
                    </div>
                    <span className="tabular-nums">
                      {Math.round(r.score)} · {r.grade}
                    </span>
                  </div>
                </td>
                <td className="py-1.5 text-xs">
                  {weakest.map(([k, d]) => (
                    <div key={k}>
                      <span className="font-medium">{LABELS[k] ?? k}:</span> {d.detail}{' '}
                      <span className="text-muted">({d.fix})</span>
                    </div>
                  ))}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </section>
  )
}

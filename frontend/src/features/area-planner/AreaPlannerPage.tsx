import { useQuery } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { useState } from 'react'
import { ConfidenceBadge } from '../../components/badges'
import { BarChart } from '../../components/charts/BarChart'
import { DataCaveats } from '../../components/DataCaveats'
import {
  getJson,
  SEGMENT_LABELS,
  type AreaKpis,
  type Confidence,
  type Projection,
  type RegionSummary,
  type StationStats,
} from '../../lib/api'
import { formatCount } from '../../lib/format'
import { POWER_BAND_COLORS, POWER_BAND_ORDER } from '../../map/powerBands'
import { EvidenceDrawer, type EvidenceTarget } from '../evidence/EvidenceDrawer'
import { ProjectionChart } from './ProjectionChart'

const YEARS = Array.from({ length: 10 }, (_, i) => 2026 + i)
const SCENARIOS = [
  { id: 'slow', label: 'Slow adoption' },
  { id: 'base', label: 'Base' },
  { id: 'fast', label: 'Fast adoption' },
]

function Kpi({
  label,
  value,
  detail,
  confidence,
}: {
  label: string
  value: string
  detail?: string
  confidence?: Confidence
}) {
  return (
    <div className="flex flex-col gap-1 rounded border border-line bg-panel p-3">
      <div className="text-xs text-muted">{label}</div>
      <div className="text-2xl font-semibold tabular-nums">{value}</div>
      {detail && <div className="text-xs text-muted">{detail}</div>}
      {confidence && (
        <div>
          <ConfidenceBadge value={confidence} />
        </div>
      )}
    </div>
  )
}

/** Area Planner (Section 3, module A): KPIs and EV projection for an area and year. */
export function AreaPlannerPage({ regionId }: { regionId: number }) {
  const navigate = useNavigate()
  const [year, setYear] = useState(2030)
  const [scenario, setScenario] = useState('base')
  const [evidence, setEvidence] = useState<EvidenceTarget>(null)

  const regions = useQuery({
    queryKey: ['regions'],
    queryFn: () => getJson<RegionSummary[]>('/regions'),
  })
  const kpis = useQuery({
    queryKey: ['kpis', regionId, year, scenario],
    queryFn: () => getJson<AreaKpis>(`/regions/${regionId}/kpis?year=${year}&scenario=${scenario}`),
  })
  const projection = useQuery({
    queryKey: ['projection', regionId, scenario],
    queryFn: () => getJson<Projection>(`/regions/${regionId}/ev-projection?scenario=${scenario}`),
  })
  const stations = useQuery({
    queryKey: ['station-stats', regionId],
    queryFn: () => getJson<StationStats>(`/stations/stats?region_id=${regionId}`),
  })

  const k = kpis.data
  const bands = [...(stations.data?.by_power_band ?? [])].sort(
    (a, b) => POWER_BAND_ORDER.indexOf(a.key) - POWER_BAND_ORDER.indexOf(b.key),
  )

  return (
    <div className="relative h-full overflow-y-auto">
      <main className="mx-auto flex max-w-6xl flex-col gap-5 p-6">
        <div className="flex flex-wrap items-end gap-4">
          <div>
            <h1 className="text-xl font-semibold">Area planner</h1>
            <p className="text-sm text-muted">
              EV fleet, public charging demand and the gap existing chargers leave.
            </p>
          </div>
          <label className="ml-auto flex flex-col text-xs text-muted">
            Area
            <select
              className="mt-1 rounded border border-line bg-panel px-2 py-1 text-sm text-ink"
              value={regionId}
              onChange={(e) =>
                navigate({ to: '/areas/$regionId', params: { regionId: e.target.value } })
              }
            >
              {regions.data?.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.name} ({r.level})
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col text-xs text-muted">
            Adoption
            <select
              className="mt-1 rounded border border-line bg-panel px-2 py-1 text-sm text-ink"
              value={scenario}
              onChange={(e) => setScenario(e.target.value)}
            >
              {SCENARIOS.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.label}
                </option>
              ))}
            </select>
          </label>
        </div>

        <div role="tablist" aria-label="Target year" className="flex flex-wrap gap-1">
          {YEARS.map((y) => (
            <button
              key={y}
              type="button"
              role="tab"
              aria-selected={y === year}
              onClick={() => setYear(y)}
              className={`rounded px-3 py-1 text-sm tabular-nums ${
                y === year ? 'bg-ink text-paper' : 'border border-line bg-panel text-ink'
              }`}
            >
              {y}
            </button>
          ))}
        </div>

        {kpis.isError && (
          <p className="text-sm text-warn">
            Couldn’t load this area. If no demand run exists yet, run <code>make demand</code>.
          </p>
        )}
        {k && (
          <>
            <DataCaveats run={k.run} />
            {k.area.coverage_share < 0.95 && (
              <div className="rounded border border-warn px-3 py-2 text-sm text-warn">
                Only {Math.round(k.area.coverage_share * 100)}% of {k.area.name} lies inside the
                modelled Pune region, so its figures cover that part only.
              </div>
            )}

            <section className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6">
              <Kpi
                label="Population"
                value={formatCount(k.population)}
                detail={`WorldPop ${k.population_year}`}
                confidence={k.confidence.population}
              />
              <Kpi
                label="Estimated EVs today"
                value={formatCount(k.current_evs)}
                detail={`as of ${k.current_evs_as_of}`}
                confidence={k.confidence.ev_stock}
              />
              <Kpi
                label={`Projected EVs ${k.year}`}
                value={formatCount(k.projected_evs.p50)}
                detail={`P10–P90: ${formatCount(k.projected_evs.p10)}–${formatCount(k.projected_evs.p90)}`}
                confidence={k.confidence.ev_stock}
              />
              <Kpi
                label="Existing public charge points"
                value={formatCount(k.existing.charge_points_incl_assumed)}
                detail={`${k.existing.stations} stations; ${k.existing.stations_without_counts} list no count (1 assumed each)`}
                confidence={k.confidence.existing_supply}
              />
              <Kpi
                label="Additional charge points needed"
                value={formatCount(k.additional_charge_points)}
                detail={`to close a ${formatCount(k.gap_kwh_per_day)} kWh/day gap`}
                confidence={k.confidence.gap}
              />
              <Kpi
                label="EVs per public charge point"
                value={
                  k.evs_per_public_charge_point === null
                    ? '—'
                    : formatCount(Math.round(k.evs_per_public_charge_point))
                }
                detail={`in ${k.year}, against today’s chargers`}
                confidence={k.confidence.ev_stock}
              />
            </section>

            <section className="rounded border border-line bg-panel p-4">
              <div className="flex items-baseline justify-between">
                <h2 className="text-sm font-semibold">Projected EV stock</h2>
                <button
                  type="button"
                  className="text-xs underline"
                  onClick={() => setEvidence({ kind: 'evidence', ids: k.evidence_ids })}
                >
                  Sources and method
                </button>
              </div>
              {projection.data && <ProjectionChart data={projection.data} selectedYear={year} />}
            </section>

            <section className="grid gap-4 md:grid-cols-2">
              <div className="rounded border border-line bg-panel p-4">
                <h2 className="mb-2 text-sm font-semibold">By segment</h2>
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-xs text-muted">
                      <th className="py-1">Segment</th>
                      <th className="py-1 text-right">Today</th>
                      <th className="py-1 text-right">{k.year} (P50)</th>
                      <th className="py-1 text-right">P10–P90</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(k.projected_evs_by_segment)
                      .sort((a, b) => b[1].p50 - a[1].p50)
                      .map(([seg, band]) => (
                        <tr key={seg} className="border-t border-line">
                          <td className="py-1">{SEGMENT_LABELS[seg] ?? seg}</td>
                          <td className="py-1 text-right tabular-nums">
                            {formatCount(k.current_evs_by_segment[seg] ?? 0)}
                          </td>
                          <td className="py-1 text-right tabular-nums">{formatCount(band.p50)}</td>
                          <td className="py-1 text-right text-xs text-muted tabular-nums">
                            {formatCount(band.p10)}–{formatCount(band.p90)}
                          </td>
                        </tr>
                      ))}
                  </tbody>
                </table>
                <p className="mt-3 text-xs text-muted">
                  Public charging demand in {k.year}: {formatCount(k.public_kwh_per_day.p50)}{' '}
                  kWh/day (P10–P90 {formatCount(k.public_kwh_per_day.p10)}–
                  {formatCount(k.public_kwh_per_day.p90)}), about {formatCount(k.sessions_per_day)}{' '}
                  sessions/day.
                </p>
              </div>

              <div className="flex flex-col gap-3 rounded border border-line bg-panel p-4">
                <h2 className="text-sm font-semibold">Existing chargers in this area</h2>
                {stations.data && (
                  <>
                    <BarChart
                      title="By operator (stations)"
                      rows={stations.data.by_operator}
                      metric="stations"
                    />
                    <BarChart
                      title="By power band (charge points)"
                      rows={bands}
                      metric="charge_points"
                      colors={POWER_BAND_COLORS}
                    />
                    <BarChart
                      title="By connector (connectors)"
                      rows={stations.data.by_connector}
                      metric="connectors"
                    />
                  </>
                )}
              </div>
            </section>
          </>
        )}
      </main>
      <EvidenceDrawer target={evidence} onClose={() => setEvidence(null)} />
    </div>
  )
}

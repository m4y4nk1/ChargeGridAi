/**
 * Digital-twin scenarios (Phase 12): region-wide what-ifs on the demand grid, with the
 * brief's three questions as presets. Screening estimates, not site plans.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { ConfidenceBadge } from '../../components/badges'
import type { Confidence } from '../../lib/api'
import { getJson, postJson } from '../../lib/api'
import { formatCount, formatPercent } from '../../lib/format'

type Kind = 'add_chargers' | 'adoption' | 'grid_constraints'

interface TwinRun {
  id: string
  kind: Kind
  params: Record<string, unknown>
  created_at: string
  result: {
    regions: string[]
    caveats: string[]
    scope_note: string
    demand_confidence: Confidence
    placement?: {
      n_requested: number
      placed: number
      unplaced: number
      demand_limited: boolean
      cells_used: number
      gap_before_kwh: number
      gap_after_kwh: number
      served_added_kwh: number
      gap_closed_share: number | null
      kwh_per_charger_per_day: number
      by_region: {
        region_id: string
        name: string
        chargers: number
        gap_before_kwh: number
        served_added_kwh: number
        gap_after_kwh: number
      }[]
      top_cells?: {
        h3: string
        chargers: number
        served_added_kwh: number
        lat: number | null
        lng: number | null
      }[]
    }
    grid?: {
      mode: string
      confidence: Confidence
      note: string
      substations_total: number
      substations: {
        osm_id: string
        voltage_kv: number | null
        chargers: number
        added_load_kw: number
      }[]
      constrained: { asset_id: string; name: string | null; utilisation_with: number }[]
    }
    adoption?: {
      multiplier: number
      year: number
      public_kwh: { base: number; what_if: number; delta: number }
      gap_kwh: { base: number; what_if: number; delta: number }
      evs: { base: number; what_if: number; delta: number }
      charge_points_to_close_gap: { base: number; what_if: number; delta: number }
      new_charge_point_kw: number
    }
  }
}

const PRESETS: { label: string; body: Record<string, unknown> }[] = [
  {
    label: 'Add 10,000 fast chargers',
    body: { kind: 'add_chargers', n: 10000, charger_class: 'DC_60', year: 2030 },
  },
  { label: 'Adoption +30%', body: { kind: 'adoption', multiplier: 1.3, year: 2030 } },
  {
    label: 'Where does the grid constrain?',
    body: { kind: 'grid_constraints', n: 10000, charger_class: 'DC_60', year: 2030 },
  },
]

const card = 'rounded border border-line bg-panel px-3 py-2'

function Stat({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className={card}>
      <p className="text-xs text-muted">{label}</p>
      <p className="text-lg font-semibold tabular-nums">{value}</p>
      {note && <p className="text-xs text-muted">{note}</p>}
    </div>
  )
}

function Result({ run }: { run: TwinRun }) {
  const r = run.result
  const p = r.placement
  const a = r.adoption
  return (
    <section aria-label="Scenario result" className="flex flex-col gap-4">
      <div
        role="note"
        className="flex flex-wrap items-center gap-2 rounded border border-warn bg-warn/10 px-3 py-2 text-sm"
      >
        <span>Demand</span>
        <ConfidenceBadge value={r.demand_confidence} />
        <span className="text-muted">{r.caveats.join(' ')}</span>
      </div>
      <p className="text-xs text-muted">
        Regions: {r.regions.join(', ')}. {r.scope_note}
      </p>
      {p && (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Stat
              label="Chargers placed"
              value={formatCount(p.placed)}
              note={
                p.demand_limited
                  ? `${formatCount(p.unplaced)} not needed: demand runs out first`
                  : `on ${formatCount(p.cells_used)} cells`
              }
            />
            <Stat
              label="Unserved demand served"
              value={`${formatCount(p.served_added_kwh)} kWh/day`}
              note={`${formatCount(p.kwh_per_charger_per_day)} kWh/day per charger`}
            />
            <Stat
              label="Gap closed"
              value={p.gap_closed_share == null ? '—' : formatPercent(p.gap_closed_share)}
              note={`${formatCount(p.gap_before_kwh)} → ${formatCount(p.gap_after_kwh)} kWh/day`}
            />
            <Stat label="Cells used" value={formatCount(p.cells_used)} />
          </div>
          <table className="w-full text-sm tabular-nums">
            <thead>
              <tr className="text-left text-xs text-muted">
                <th>Region</th>
                <th>Chargers</th>
                <th>Gap before</th>
                <th>Served</th>
                <th>Gap after</th>
              </tr>
            </thead>
            <tbody>
              {p.by_region.map((b) => (
                <tr key={b.region_id} className="border-t border-line">
                  <td>{b.name}</td>
                  <td>{formatCount(b.chargers)}</td>
                  <td>{formatCount(b.gap_before_kwh)} kWh/d</td>
                  <td>{formatCount(b.served_added_kwh)} kWh/d</td>
                  <td>{formatCount(b.gap_after_kwh)} kWh/d</td>
                </tr>
              ))}
            </tbody>
          </table>
          {p.top_cells && p.top_cells.length > 0 && (
            <details className="text-xs">
              <summary>Cells receiving the most chargers</summary>
              <table className="mt-1 w-full tabular-nums">
                <tbody>
                  {p.top_cells.map((c) => (
                    <tr key={c.h3} className="border-t border-line">
                      <td className="font-mono">{c.h3}</td>
                      <td>{c.chargers} chargers</td>
                      <td>{formatCount(c.served_added_kwh)} kWh/d</td>
                      <td>{c.lat != null && `${c.lat}, ${c.lng}`}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </details>
          )}
        </>
      )}
      {r.grid && (
        <section aria-label="Grid constraints" className="flex flex-col gap-2">
          <h2 className="flex items-center gap-2 text-sm font-semibold">
            Load at the nearest substations <ConfidenceBadge value={r.grid.confidence} />
          </h2>
          <p className="text-xs text-muted">{r.grid.note}</p>
          {r.grid.constrained.length > 0 && (
            <p className="text-sm text-warn">
              {r.grid.constrained.length} rated substation(s) would exceed their loading limit.
            </p>
          )}
          <table className="w-full text-sm tabular-nums">
            <thead>
              <tr className="text-left text-xs text-muted">
                <th>Substation (OSM)</th>
                <th>Chargers</th>
                <th>Added load</th>
              </tr>
            </thead>
            <tbody>
              {r.grid.substations.map((s) => (
                <tr key={s.osm_id} className="border-t border-line">
                  <td>
                    {s.osm_id}
                    {s.voltage_kv ? ` · ${s.voltage_kv} kV` : ''}
                  </td>
                  <td>{formatCount(s.chargers)}</td>
                  <td>{formatCount(s.added_load_kw)} kW</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="text-xs text-muted">
            Top 20 of {formatCount(r.grid.substations_total)} substations.
          </p>
        </section>
      )}
      {a && (
        <table className="w-full text-sm tabular-nums" aria-label="Adoption what-if">
          <thead>
            <tr className="text-left text-xs text-muted">
              <th>{a.year}</th>
              <th>Base</th>
              <th>Adoption ×{a.multiplier}</th>
              <th>Change</th>
            </tr>
          </thead>
          <tbody>
            {(
              [
                ['EVs (P50)', a.evs, ''],
                ['Public charging demand', a.public_kwh, ' kWh/d'],
                ['Unserved gap', a.gap_kwh, ' kWh/d'],
                [
                  `Charge points to close the gap (${a.new_charge_point_kw} kW)`,
                  a.charge_points_to_close_gap,
                  '',
                ],
              ] as const
            ).map(([label, v, unit]) => (
              <tr key={label} className="border-t border-line">
                <td>{label}</td>
                <td>
                  {formatCount(v.base)}
                  {unit}
                </td>
                <td>
                  {formatCount(v.what_if)}
                  {unit}
                </td>
                <td>
                  {v.delta >= 0 ? '+' : ''}
                  {formatCount(v.delta)}
                  {unit}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}

export function TwinPage() {
  const qc = useQueryClient()
  const history = useQuery({ queryKey: ['twin'], queryFn: () => getJson<TwinRun[]>('/twin/runs') })
  const [selected, setSelected] = useState<string | null>(null)
  const run = useMutation({
    mutationFn: (body: Record<string, unknown>) => postJson<TwinRun>('/twin/runs', body),
    onSuccess: (r) => {
      setSelected(r.id)
      void qc.invalidateQueries({ queryKey: ['twin'] })
    },
  })
  const current =
    (history.data ?? []).find((r) => r.id === selected) ?? run.data ?? history.data?.[0]
  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto flex max-w-5xl flex-col gap-4 p-6">
        <h1 className="text-lg font-semibold">Region-wide scenarios</h1>
        <p className="text-sm text-muted">
          Quick what-ifs across every onboarded region, computed on the demand grid. Use a site plan
          for where exactly to build.
        </p>
        <div className="flex flex-wrap gap-2">
          {PRESETS.map((p) => (
            <button
              key={p.label}
              type="button"
              disabled={run.isPending}
              onClick={() => run.mutate(p.body)}
              className="rounded border border-line px-3 py-1.5 text-sm hover:bg-panel disabled:opacity-50"
            >
              {p.label}
            </button>
          ))}
          {run.isPending && <span className="self-center text-sm text-muted">Running…</span>}
        </div>
        {run.error && (
          <p role="alert" className="text-sm text-warn">
            {run.error.message}
          </p>
        )}
        {current && <Result run={current} />}
        {(history.data?.length ?? 0) > 1 && (
          <details className="text-xs">
            <summary>Earlier scenarios</summary>
            <ul>
              {history.data!.map((h) => (
                <li key={h.id}>
                  <button type="button" className="underline" onClick={() => setSelected(h.id)}>
                    {h.kind.replace('_', ' ')} · {new Date(h.created_at).toLocaleString()}
                  </button>
                </li>
              ))}
            </ul>
          </details>
        )}
      </div>
    </div>
  )
}

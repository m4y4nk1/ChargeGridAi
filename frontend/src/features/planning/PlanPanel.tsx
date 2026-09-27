import { useMutation, useQuery } from '@tanstack/react-query'
import type { FeatureCollection, Point } from 'geojson'
import { useEffect, useMemo, useState } from 'react'
import {
  getJson,
  postJson,
  type CellAssignment,
  type OptimisationDetail,
  type OptimisationSummary,
  type PlanningRun,
  type PlanSite,
  type SiteProperties,
  type SubScore,
  type WhyNot,
} from '../../lib/api'
import { formatCount, formatCrore, formatLakh, formatPercent } from '../../lib/format'
import { siteTitle } from '../../lib/planning'
import { SUB_SCORE_LABELS } from '../../lib/scoring'
import { CATEGORY_COLORS, categoricalCells } from '../../map/hexLayer'

export type CatchmentMode = 'off' | 'assignment' | 'voronoi'

export interface PlanOverlay {
  sites: FeatureCollection<Point, SiteProperties & { plan: string }> | null
  cells: FeatureCollection | null
}

const field = 'mt-1 w-full rounded border border-line bg-panel px-2 py-1 text-sm text-ink'

function Kpi({ label, value, detail }: { label: string; value: string; detail?: string }) {
  return (
    <div className="rounded border border-line bg-panel p-2">
      <div className="text-[11px] text-muted">{label}</div>
      <div className="text-lg font-semibold tabular-nums">{value}</div>
      {detail && <div className="text-[11px] text-muted">{detail}</div>}
    </div>
  )
}

function SolverLine({ opt }: { opt: OptimisationSummary }) {
  const s = opt.solver_stats
  const gap = s.mip_gap === null ? 'no bound' : `gap ${formatPercent(s.mip_gap, 2)}`
  return (
    <p className="text-[11px] text-muted" title="OR-Tools solver, status, time and optimality gap">
      {s.solver} · {opt.status} · {gap} · {s.solve_s.toFixed(1)} s
      {s.total_s !== undefined && ` (${s.total_s.toFixed(1)} s end to end)`}
    </p>
  )
}

function toFeature(site: PlanSite, plan: string) {
  return {
    type: 'Feature' as const,
    geometry: { type: 'Point' as const, coordinates: [site.lng, site.lat] },
    properties: {
      id: site.site_id,
      origin: site.origin,
      merged_origins: [],
      host_type: site.host_type,
      name: site.name,
      status: 'feasible' as const,
      reason_codes: [],
      rank: site.rank,
      score_total: null,
      selected: true,
      plan,
      label: 'Candidate site — requires field verification',
    },
  }
}

function WhyNotItem({
  site,
  onSite,
}: {
  site: { site_id: string; name: string | null; host_type: string; rank: number; why_not: WhyNot }
  onSite: (id: string) => void
}) {
  const w = site.why_not
  const rival = w.displaced[0]
  return (
    <li>
      <button
        type="button"
        onClick={() => onSite(site.site_id)}
        className="w-full rounded border border-line p-1.5 text-left text-xs hover:border-ink"
      >
        <div className="flex justify-between gap-2">
          <span className="font-medium">{siteTitle(site)}</span>
          <span className="text-muted">rank {site.rank}</span>
        </div>
        <div className="text-muted">
          {w.status === 'infeasible'
            ? 'Can’t be added within the budget and site limit.'
            : rival
              ? `Would replace ${rival.name ?? 'site #' + rival.rank}; ${
                  w.deciding_sub_score !== 'none'
                    ? `trails it on ${SUB_SCORE_LABELS[w.deciding_sub_score as SubScore] ?? w.deciding_sub_score}`
                    : 'no sub-score behind'
                }.`
              : 'Fits alongside the plan, but adds less than it costs.'}{' '}
          {w.served_delta_kwh !== 0 &&
            `Served demand ${w.served_delta_kwh > 0 ? '+' : ''}${formatCount(Math.round(w.served_delta_kwh))} kWh/day. `}
          {w.spacing_conflicts.length > 0 &&
            `Too close to ${w.spacing_conflicts.map((c) => c.name ?? 'a selected site').join(', ')}.`}
        </div>
      </button>
    </li>
  )
}

/** Plan tab (Section 9.7): base plan, what-if with diff, catchments (module D), why-not. */
export function PlanPanel({
  run,
  onSite,
  onOverlay,
}: {
  run: PlanningRun
  onSite: (id: string, optimisationId?: string) => void
  onOverlay: (overlay: PlanOverlay) => void
}) {
  const baseId = run.funnel.optimisation?.base_id ?? null
  const base = useQuery({
    queryKey: ['optimisation', baseId],
    queryFn: () => getJson<OptimisationDetail>(`/optimisations/${baseId}`),
    enabled: !!baseId,
  })
  const whyNot = useQuery({
    queryKey: ['why-not', run.id],
    queryFn: () =>
      getJson<
        { site_id: string; name: string | null; host_type: string; rank: number; why_not: WhyNot }[]
      >(`/runs/${run.id}/why-not`),
    enabled: !!baseId,
  })

  const [budgetCr, setBudgetCr] = useState('')
  const [maxSites, setMaxSites] = useState('')
  const [multiplier, setMultiplier] = useState(String(run.scenario.adoption_multiplier))
  const [mode, setMode] = useState<CatchmentMode>('off')

  const whatif = useMutation({
    mutationFn: () =>
      postJson<OptimisationDetail>(`/runs/${run.id}/whatif`, {
        budget_inr: budgetCr ? Math.round(Number(budgetCr) * 1e7) : null,
        max_sites: maxSites ? Number(maxSites) : null,
        adoption_multiplier: Number(multiplier),
      }),
  })
  const shown = whatif.data ?? base.data
  const cells = useQuery({
    queryKey: ['cells', shown?.id, mode],
    queryFn: () => getJson<CellAssignment>(`/optimisations/${shown?.id}/cells?mode=${mode}`),
    enabled: !!shown && mode !== 'off',
    staleTime: Infinity,
  })

  const colorOf = useMemo(() => {
    const index = new Map(shown?.sites.map((s, i) => [s.site_id, i]))
    return (siteId: string) => {
      const i = index.get(siteId)
      return i === undefined ? null : CATEGORY_COLORS[i % CATEGORY_COLORS.length]
    }
  }, [shown])

  useEffect(() => {
    if (!shown || !base.data) {
      onOverlay({ sites: null, cells: null })
      return
    }
    const d = whatif.data?.diff
    const added = new Set(d?.added.map((a) => a.site_id))
    const features = shown.sites.map((s) =>
      toFeature(s, d ? (added.has(s.site_id) ? 'added' : 'kept') : 'selected'),
    )
    if (d) {
      const removed = new Set(d.removed.map((r) => r.site_id))
      for (const s of base.data.sites)
        if (removed.has(s.site_id)) features.push(toFeature(s, 'removed'))
    }
    const fill =
      mode !== 'off' && cells.data
        ? categoricalCells(
            Object.fromEntries(Object.entries(cells.data.cells).map(([h, c]) => [h, c.site_id])),
            colorOf,
          )
        : null
    onOverlay({ sites: { type: 'FeatureCollection', features }, cells: fill })
  }, [shown, base.data, whatif.data, mode, cells.data, colorOf, onOverlay])

  if (!baseId) {
    return (
      <p className="text-sm text-muted">
        {run.funnel.optimisation?.skipped ??
          'No plan for this run. Set a budget on the scenario to optimise.'}
      </p>
    )
  }
  if (!base.data) return <p className="text-sm text-muted">Loading plan…</p>
  const k = shown!.kpis
  const budget = shown!.params.budget_inr
  const diff = whatif.data?.diff

  return (
    <section aria-label="Plan" className="flex flex-col gap-4">
      <div>
        <h2 className="text-sm font-semibold">
          {whatif.data ? 'What-if plan' : 'Scenario plan'} · {run.scenario.target_year}
        </h2>
        <SolverLine opt={shown!} />
      </div>
      <div className="grid grid-cols-2 gap-2">
        <Kpi label="Sites" value={String(k.n_sites)} detail={`limit ${shown!.params.max_sites}`} />
        <Kpi
          label="Charge points"
          value={String(k.n_charge_points)}
          detail={Object.entries(k.charger_mix)
            .map(([c, n]) => `${n}× ${c.replace('_', ' ')} kW`)
            .join(', ')}
        />
        <Kpi
          label="Cost (CAPEX)"
          value={formatCrore(k.cost_inr)}
          detail={`of ${formatCrore(budget)} budget`}
        />
        <Kpi
          label="Unmet demand served"
          value={formatPercent(k.coverage_share)}
          detail={`${formatCount(Math.round(k.coverage_kwh))} of ${formatCount(Math.round(k.demand_kwh))} kWh/day`}
        />
        <Kpi
          label="Underserved people in reach"
          value={formatPercent(k.equity)}
          detail="inside a selected site’s catchment"
        />
        <Kpi
          label="Utilisation"
          value={formatPercent(k.utilisation)}
          detail={
            k.utilisation && k.utilisation > 0.99
              ? 'capacity-bound: demand exceeds supply'
              : undefined
          }
        />
      </div>
      <p className="text-[11px] text-synthetic">
        Costs are illustrative placeholders ({shown!.params.placeholders.length} assumptions) and
        demand is synthetic, so treat this as a demonstration of the method.
      </p>

      {k.economics && (
        <div
          className="rounded border border-line bg-panel p-2 text-xs"
          aria-label="Plan economics"
        >
          <p className="font-medium">
            Sized: {k.economics.chargers_sized} chargers vs {k.n_charge_points} in the plan · CAPEX{' '}
            {formatCrore(k.economics.capex_inr)}
          </p>
          <p className="text-muted">
            Base-case NPV across sites {formatCrore(k.economics.npv_base_case_inr)} ·{' '}
            {k.economics.sites_npv_positive_p50} of {k.economics.sites} sites positive at P50. Open
            a site’s Sizing and Finance tabs for the detail.
          </p>
        </div>
      )}

      <form
        aria-label="What-if"
        className="flex flex-col gap-2 rounded border border-line bg-panel p-3"
        onSubmit={(e) => {
          e.preventDefault()
          whatif.mutate()
        }}
      >
        <h3 className="text-xs font-semibold uppercase tracking-wide text-muted">What-if</h3>
        <div className="grid grid-cols-3 gap-2">
          <label className="text-xs text-muted">
            Budget (₹ Cr)
            <input
              className={field}
              inputMode="decimal"
              placeholder={String(base.data.params.budget_inr / 1e7)}
              value={budgetCr}
              onChange={(e) => setBudgetCr(e.target.value)}
            />
          </label>
          <label className="text-xs text-muted">
            Max sites
            <input
              className={field}
              inputMode="numeric"
              placeholder={String(base.data.params.max_sites)}
              value={maxSites}
              onChange={(e) => setMaxSites(e.target.value)}
            />
          </label>
          <label className="text-xs text-muted">
            Adoption
            <select
              className={field}
              value={multiplier}
              onChange={(e) => setMultiplier(e.target.value)}
            >
              <option value={String(run.scenario.adoption_multiplier)}>as scenario</option>
              <option value="1.3">+30%</option>
            </select>
          </label>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="submit"
            disabled={whatif.isPending}
            className="rounded bg-ink px-3 py-1 text-sm text-paper disabled:opacity-40"
          >
            {whatif.isPending ? 'Re-optimising…' : 'Re-optimise'}
          </button>
          {whatif.data && (
            <button type="button" className="text-xs underline" onClick={() => whatif.reset()}>
              Back to scenario plan
            </button>
          )}
        </div>
        {whatif.isError && <p className="text-xs text-warn">{whatif.error.message}</p>}
        {diff && (
          <div className="text-xs" aria-label="What-if changes">
            <p className="font-medium">
              Re-optimised in {whatif.data!.solver_stats.total_s?.toFixed(1)} s ·{' '}
              <span className="text-[#009E73]">+{diff.added.length} added</span> ·{' '}
              <span className="text-[#D55E00]">−{diff.removed.length} removed</span> ·{' '}
              {diff.resized.length} resized · {diff.kept} kept
            </p>
            <p className="text-muted">
              Served {diff.delta.coverage_kwh >= 0 ? '+' : ''}
              {formatCount(Math.round(diff.delta.coverage_kwh))} kWh/day · cost{' '}
              {diff.delta.cost_inr >= 0 ? '+' : '−'}
              {formatCrore(Math.abs(diff.delta.cost_inr))} · charge points{' '}
              {diff.delta.n_charge_points >= 0 ? '+' : ''}
              {diff.delta.n_charge_points}
            </p>
          </div>
        )}
      </form>

      <div
        role="radiogroup"
        aria-label="Catchments on map"
        className="flex flex-wrap gap-1 text-xs"
      >
        {(
          [
            ['off', 'No catchments'],
            ['assignment', 'Served by (plan)'],
            ['voronoi', 'Nearest by drive time'],
          ] as const
        ).map(([id, label]) => (
          <button
            key={id}
            type="button"
            role="radio"
            aria-checked={mode === id}
            onClick={() => setMode(id)}
            className={`rounded px-2 py-1 ${mode === id ? 'bg-ink text-paper' : 'border border-line bg-panel'}`}
          >
            {label}
          </button>
        ))}
        {cells.isFetching && <span className="text-muted">loading…</span>}
      </div>

      <section aria-label="Plan sites">
        <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted">
          Sites · by demand served
        </h3>
        <ul className="flex flex-col gap-1">
          {shown!.sites.map((s, i) => (
            <li key={s.site_id}>
              <button
                type="button"
                onClick={() => onSite(s.site_id, shown!.id)}
                className="flex w-full items-start gap-2 rounded border border-line p-1.5 text-left text-xs hover:border-ink"
              >
                <span
                  className="mt-0.5 inline-block h-2.5 w-2.5 shrink-0 rounded-sm"
                  style={{ background: CATEGORY_COLORS[i % CATEGORY_COLORS.length] }}
                />
                <span className="flex-1">
                  <span className="font-medium">{siteTitle(s)}</span>
                  <span className="block text-muted">
                    {s.bundle.replace('_', ' ').replace('x', ' kW × ')} · {s.connection} ·{' '}
                    {formatLakh(s.cost_inr)} · {formatCount(Math.round(s.served_kwh))} kWh/day
                  </span>
                </span>
                <span className="text-muted">#{s.rank}</span>
              </button>
            </li>
          ))}
        </ul>
      </section>

      {!whatif.data && whyNot.data && whyNot.data.length > 0 && (
        <section aria-label="Why not">
          <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted">
            Why not? · strong sites left out
          </h3>
          <ul className="flex flex-col gap-1">
            {whyNot.data.map((s) => (
              <WhyNotItem key={s.site_id} site={s} onSite={onSite} />
            ))}
          </ul>
        </section>
      )}
    </section>
  )
}

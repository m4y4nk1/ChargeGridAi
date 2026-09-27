import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { ConfidenceBadge, LicenseBadge } from '../../components/badges'
import {
  getJson,
  type EvidenceItem,
  type SiteDetail,
  type StationDetail,
  type SubScore,
  type WhyNot,
} from '../../lib/api'
import { formatCount, formatDate } from '../../lib/format'
import { SUB_SCORE_LABELS } from '../../lib/scoring'
import { EconomicsTabs } from '../planning/EconomicsTabs'
import { EnergyTab } from '../planning/EnergyTab'
import { SiteScoreBreakdown } from '../planning/ScoreParts'
import {
  describeRule,
  ORIGIN_LABELS,
  OUTCOME_LABELS,
  RULE_LABELS,
  siteTitle,
} from '../../lib/planning'

export type EvidenceTarget =
  | { kind: 'station'; stationId: string }
  | { kind: 'evidence'; ids: string[] }
  | { kind: 'site'; runId: string; siteId: string; optimisationId?: string }
  | null

const OUTCOME_STYLE: Record<string, string> = {
  pass: 'text-ink',
  fail: 'font-semibold text-warn',
  not_applied: 'text-muted',
  disabled: 'text-muted',
  deferred: 'text-muted',
}

function WhyNotText({ w }: { w: WhyNot }) {
  if (w.status === 'infeasible')
    return <p>Forcing it in breaks the budget or site limit, so the plan can’t include it.</p>
  const rival = w.displaced[0]
  return (
    <ul className="list-disc pl-4">
      <li>
        Forced into the plan, it{' '}
        {rival ? (
          <>
            displaces <b>{rival.name ?? `site #${rival.rank}`}</b> (rank {rival.rank})
          </>
        ) : (
          'joins the other sites'
        )}
        .
      </li>
      <li>
        Demand served changes by {w.served_delta_kwh >= 0 ? '+' : ''}
        {formatCount(Math.round(w.served_delta_kwh))} kWh/day; cost by{' '}
        {w.cost_delta_inr >= 0 ? '+' : '−'}₹{formatCount(Math.abs(Math.round(w.cost_delta_inr)))}.
      </li>
      {w.deciding_sub_score !== 'none' && (
        <li>
          Deciding factor: it trails on{' '}
          <b>{SUB_SCORE_LABELS[w.deciding_sub_score as SubScore] ?? w.deciding_sub_score}</b> (
          {w.deciding_points.toFixed(1)} weighted points).
        </li>
      )}
      {w.spacing_conflicts.length > 0 && (
        <li>
          Within the minimum spacing of{' '}
          {w.spacing_conflicts.map((c) => c.name ?? 'a selected site').join(', ')}.
        </li>
      )}
    </ul>
  )
}

function CandidateSiteView({
  runId,
  siteId,
  optimisationId,
}: {
  runId: string
  siteId: string
  optimisationId?: string
}) {
  const [tab, setTab] = useState<'overview' | 'sizing' | 'finance' | 'energy'>('overview')
  const { data, isError } = useQuery({
    queryKey: ['site', runId, siteId],
    queryFn: () => getJson<SiteDetail>(`/runs/${runId}/sites/${siteId}`),
  })
  if (isError) return <p className="text-sm text-warn">Couldn’t load this site.</p>
  if (!data) return <p className="text-sm text-muted">Loading…</p>
  const detail = (data.features?.origin_detail ?? {}) as Record<string, unknown>
  // Sizing and finance exist for sites in a plan: a what-if's, or the run's base plan.
  const optId = optimisationId ?? (data.plan?.selected ? data.plan.optimisation_id : null)

  return (
    <div className="flex flex-col gap-4">
      <div>
        <p className="rounded border border-dashed border-warn px-2 py-1 text-xs text-warn">
          {data.label}
        </p>
        <h3 className="mt-2 text-base font-semibold">{siteTitle(data)}</h3>
        <p className="text-sm text-muted">
          {ORIGIN_LABELS[data.origin] ?? data.origin}
          {data.host_type && ` · host ${data.host_type.replaceAll('_', ' ').toLowerCase()}`}
          {' · '}
          <span className="tabular-nums">
            {data.lat.toFixed(5)}, {data.lng.toFixed(5)}
          </span>
        </p>
        {data.merged_origins.length > 0 && (
          <p className="text-xs text-muted">
            Also proposed by: {data.merged_origins.map((o) => ORIGIN_LABELS[o] ?? o).join(', ')}
            {typeof detail.merged_count === 'number' && ` (${detail.merged_count} merged)`}
          </p>
        )}
        <p className="mt-1 text-sm font-medium">
          {data.status === 'rejected'
            ? `Rejected by ${data.feasibility?.reason_codes.join(', ')}`
            : data.status === 'feasible'
              ? 'Passed every applicable rule'
              : 'Not yet screened'}
        </p>
      </div>

      {optId && (
        <div role="tablist" aria-label="Site view" className="flex gap-1">
          {(
            [
              ['overview', 'Overview'],
              ['sizing', 'Sizing'],
              ['finance', 'Finance'],
              ['energy', 'Energy'],
            ] as const
          ).map(([id, label]) => (
            <button
              key={id}
              type="button"
              role="tab"
              aria-selected={tab === id}
              onClick={() => setTab(id)}
              className={`rounded px-3 py-1 text-sm ${
                tab === id ? 'bg-ink text-paper' : 'border border-line bg-panel'
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      )}
      {optId && tab === 'energy' ? (
        <EnergyTab optimisationId={optId} siteId={siteId} runId={runId} />
      ) : optId && (tab === 'sizing' || tab === 'finance') ? (
        <EconomicsTabs optimisationId={optId} siteId={siteId} tab={tab} />
      ) : (
        <>
          {data.plan?.selected && (
            <section className="rounded border border-ink p-2 text-sm">
              <h4 className="text-xs font-semibold uppercase tracking-wide">In the plan</h4>
              <p>
                Opens by {data.plan.phase_year} and serves{' '}
                {formatCount(Math.round(data.plan.coverage_kwh ?? 0))} kWh/day of unmet demand from{' '}
                {formatCount(data.plan.served_population)} people’s cells.
              </p>
            </section>
          )}
          {data.plan?.why_not && (
            <section className="rounded border border-dashed border-warn p-2 text-sm">
              <h4 className="text-xs font-semibold uppercase tracking-wide text-warn">Why not?</h4>
              <WhyNotText w={data.plan.why_not} />
            </section>
          )}

          {data.scoring && (
            <section>
              <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">
                Score
              </h4>
              <SiteScoreBreakdown scoring={data.scoring} />
            </section>
          )}

          {data.feasibility && (
            <section>
              <h4 className="text-xs font-semibold uppercase tracking-wide text-muted">
                Feasibility rules
              </h4>
              <ul className="mt-1 flex flex-col gap-1.5">
                {Object.entries(data.feasibility.rules).map(([code, rule]) => (
                  <li key={code} className="text-sm">
                    <div className="flex justify-between gap-2">
                      <span>
                        <span className="font-mono text-xs">{code}</span>{' '}
                        {RULE_LABELS[code] ?? code}
                      </span>
                      <span className={`text-xs ${OUTCOME_STYLE[rule.outcome] ?? ''}`}>
                        {OUTCOME_LABELS[rule.outcome]}
                      </span>
                    </div>
                    <div className="text-xs text-muted">{describeRule(rule)}</div>
                  </li>
                ))}
              </ul>
            </section>
          )}

          <section>
            <h4 className="text-xs font-semibold uppercase tracking-wide text-muted">
              Recorded evidence
            </h4>
            <div className="mt-1">
              <EvidenceList ids={data.evidence_ids} />
            </div>
          </section>
        </>
      )}
    </div>
  )
}

function StationProvenance({ stationId }: { stationId: string }) {
  const { data, isError } = useQuery({
    queryKey: ['station', stationId],
    queryFn: () => getJson<StationDetail>(`/stations/${stationId}`),
  })
  if (isError) return <p className="text-sm text-warn">Couldn’t load this station.</p>
  if (!data) return <p className="text-sm text-muted">Loading…</p>

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h3 className="text-base font-semibold">{data.name ?? 'Unnamed station'}</h3>
        <p className="text-sm text-muted">{data.operator ?? 'Operator unknown'}</p>
        <div className="mt-2 flex flex-wrap gap-1">
          <ConfidenceBadge value={data.confidence} />
          <LicenseBadge value={data.license_class} />
        </div>
      </div>

      <section>
        <h4 className="text-xs font-semibold uppercase tracking-wide text-muted">Charge points</h4>
        {data.evses.length === 0 ? (
          <p className="text-sm text-muted">
            No source lists charge points or connectors for this station.
          </p>
        ) : (
          <table className="mt-1 w-full text-sm">
            <tbody>
              {data.evses.map((e, i) => (
                <tr key={i} className="border-b border-line last:border-0">
                  <td className="py-1 tabular-nums">{e.count}×</td>
                  <td className="py-1">{e.power_band}</td>
                  <td className="py-1 text-right tabular-nums">
                    {e.max_kw !== null ? `${e.max_kw} kW` : '— kW'}
                  </td>
                  <td className="py-1 pl-2 text-xs text-muted">
                    {e.connectors.map((c) => c.standard).join(', ')}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      {data.conflicts.length > 0 && (
        <section>
          <h4 className="text-xs font-semibold uppercase tracking-wide text-warn">
            Sources disagree
          </h4>
          <ul className="mt-1 text-sm">
            {data.conflicts.map((c) => (
              <li key={c.field}>
                <span className="font-medium">{c.field.replaceAll('_', ' ')}:</span>{' '}
                {Object.entries(c.values)
                  .map(([source, value]) => `${source} says ${String(value)}`)
                  .join('; ')}
              </li>
            ))}
          </ul>
        </section>
      )}

      <section>
        <h4 className="text-xs font-semibold uppercase tracking-wide text-muted">
          Built from {data.sources.length} source record{data.sources.length === 1 ? '' : 's'}
        </h4>
        <ul className="mt-1 flex flex-col gap-2">
          {data.sources.map((s) => (
            <li
              key={`${s.source_id}:${s.source_record_id}`}
              className="rounded border border-line p-2 text-sm"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="font-medium">{s.source_name}</span>
                <LicenseBadge value={s.license_class} />
              </div>
              <div className="mt-1 text-xs text-muted">
                Record {s.source_record_id} · retrieved {formatDate(s.retrieved_at)} · match{' '}
                {Math.round(s.match_score * 100)}%
              </div>
              {s.attribution && <div className="text-xs text-muted">{s.attribution}</div>}
            </li>
          ))}
        </ul>
      </section>
      <p className="text-xs text-muted">
        Existing station — locations and counts as reported by sources.
      </p>
    </div>
  )
}

function EvidenceList({ ids }: { ids: string[] }) {
  const { data, isError } = useQuery({
    queryKey: ['evidence', ids],
    queryFn: () => getJson<EvidenceItem[]>(`/evidence?ids=${ids.join(',')}`),
    enabled: ids.length > 0,
  })
  if (ids.length === 0) return <p className="text-sm text-muted">No evidence recorded yet.</p>
  if (isError) return <p className="text-sm text-warn">Couldn’t load evidence.</p>
  if (!data) return <p className="text-sm text-muted">Loading…</p>
  return (
    <ul className="flex flex-col gap-2">
      {data.map((e) => (
        <li key={e.id} className="rounded border border-line p-2 text-sm">
          <div className="flex items-center justify-between gap-2">
            <span className="font-medium">{e.metric.replaceAll('_', ' ')}</span>
            <span className="tabular-nums">
              {formatCount(e.value_num)} {e.unit}
            </span>
          </div>
          <div className="mt-1 text-xs text-muted">
            {e.method} · computed {formatDate(e.created_at)}
          </div>
          <div className="mt-1 flex flex-wrap gap-1">
            <ConfidenceBadge value={e.confidence} />
            {e.sources.map((s) => (
              <span key={s.snapshot_id} className="text-xs text-muted">
                {s.source_name} ({formatDate(s.retrieved_at)})
              </span>
            ))}
          </div>
        </li>
      ))}
    </ul>
  )
}

/** Evidence drawer (Section 3): sources, freshness, licence and confidence behind what's on screen. */
export function EvidenceDrawer({
  target,
  onClose,
}: {
  target: EvidenceTarget
  onClose: () => void
}) {
  if (target === null) return null
  return (
    <aside
      aria-label="Evidence"
      className={`absolute top-0 right-0 z-10 flex h-full max-w-full flex-col border-l border-line bg-panel shadow-lg ${
        target.kind === 'site' ? 'w-[30rem]' : 'w-96'
      }`}
    >
      <div className="flex items-center justify-between border-b border-line px-4 py-2">
        <h2 className="text-sm font-semibold">Evidence</h2>
        <button type="button" onClick={onClose} className="text-sm underline">
          Close
        </button>
      </div>
      <div className="overflow-y-auto p-4">
        {target.kind === 'station' ? (
          <StationProvenance stationId={target.stationId} />
        ) : target.kind === 'site' ? (
          <CandidateSiteView
            key={`${target.siteId}:${target.optimisationId ?? ''}`}
            runId={target.runId}
            siteId={target.siteId}
            optimisationId={target.optimisationId}
          />
        ) : (
          <EvidenceList ids={target.ids} />
        )}
      </div>
    </aside>
  )
}

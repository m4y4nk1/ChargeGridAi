import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import type { FeatureCollection, Point } from 'geojson'
import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  getJson,
  streamUrl,
  type PlanningRun,
  type Ranking,
  type RejectedSite,
  type SiteProperties,
} from '../../lib/api'
import { formatCount } from '../../lib/format'
import {
  describeRule,
  funnelStages,
  ORIGIN_LABELS,
  RULE_LABELS,
  siteTitle,
} from '../../lib/planning'
import { OpenMapCanvas, SITE_COLORS } from '../../map/OpenMapCanvas'
import { EvidenceDrawer, type EvidenceTarget } from '../evidence/EvidenceDrawer'
import { ComparePanel } from './ComparePanel'
import { ExportButtons } from './ExportButtons'
import { PhasingPanel } from './PhasingPanel'
import { PlanPanel, type PlanOverlay } from './PlanPanel'
import { RankingPanel } from './RankingPanel'
import { StrategiesPanel } from './StrategiesPanel'
import { BasemapToggle } from '../../map/BasemapToggle'
import { GoogleLayersMap } from '../../map/GoogleLayersMap'
import { useBasemap } from '../../map/useBasemap'

type Tab = 'plan' | 'strategies' | 'phasing' | 'ranking' | 'funnel'
const TABS: [Tab, string][] = [
  ['plan', 'Plan'],
  ['strategies', 'Strategies'],
  ['ranking', 'Ranked sites'],
  ['phasing', 'Phasing'],
  ['funnel', 'Funnel'],
]

const FINISHED = new Set(['succeeded', 'failed'])

/** Live run progress over SSE (Section 10.4); each event refreshes the run from the API. */
function useRunEvents(runId: string, finished: boolean) {
  const queryClient = useQueryClient()
  useEffect(() => {
    if (finished) return
    const source = new EventSource(streamUrl(`/runs/${runId}/events`))
    const refresh = () => void queryClient.invalidateQueries({ queryKey: ['run', runId] })
    for (const kind of ['step_started', 'progress']) source.addEventListener(kind, refresh)
    source.addEventListener('completed', () => {
      source.close()
      refresh()
      void queryClient.invalidateQueries({ queryKey: ['run-sites', runId] })
      void queryClient.invalidateQueries({ queryKey: ['run-rejected', runId] })
    })
    // Server-sent `error` events carry data; a bare connection error doesn't. Either way the
    // polling fallback in RunPage keeps the view current, so don't let EventSource retry forever.
    source.addEventListener('error', (e) => {
      if (!(e instanceof MessageEvent)) source.close()
      refresh()
    })
    return () => source.close()
  }, [runId, finished, queryClient])
}

function Steps({ run }: { run: PlanningRun }) {
  // Optimisation steps appear only for runs with a budget, so follow the run's own log.
  const steps = [
    ...new Set([
      'resolve_data',
      'candidates',
      'feasibility',
      'catchments',
      'scoring',
      ...run.progress.map((p) => p.step).filter((s) => s !== 'run'),
    ]),
  ]
  const state = (step: string) => {
    const entries = run.progress.filter((p) => p.step === step)
    return entries.at(-1)?.status ?? 'waiting'
  }
  return (
    <ol className="flex flex-col gap-1 text-sm" aria-label="Run steps">
      {steps.map((s) => {
        const st = state(s)
        return (
          <li key={s} className="flex items-center gap-2">
            <span
              aria-hidden
              className={`inline-block h-2 w-2 rounded-full ${
                st === 'completed'
                  ? 'bg-ink'
                  : st === 'failed'
                    ? 'bg-warn'
                    : st === 'started'
                      ? 'animate-pulse bg-muted'
                      : 'border border-muted'
              }`}
            />
            <span>{s.replace('_', ' ')}</span>
            <span className="ml-auto text-xs text-muted">{st}</span>
          </li>
        )
      })}
    </ol>
  )
}

function FunnelBars({ run }: { run: PlanningRun }) {
  const stages = funnelStages(run.funnel)
  const top = stages[0].value ?? 0
  return (
    <section aria-label="Funnel">
      <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">Funnel</h2>
      <ul className="flex flex-col gap-1.5">
        {stages.map((s) => (
          <li key={s.key}>
            <div className="flex justify-between text-xs">
              <span>{s.label}</span>
              <span className="tabular-nums">{s.value === null ? '—' : formatCount(s.value)}</span>
            </div>
            <div className="mt-0.5 h-2 rounded bg-line">
              {s.value !== null && top > 0 && (
                <div
                  className="h-2 rounded bg-ink"
                  style={{ width: `${(100 * s.value) / top}%` }}
                />
              )}
            </div>
            {s.pending && <div className="text-[11px] text-muted">{s.pending}</div>}
          </li>
        ))}
      </ul>
      {run.funnel.candidates && (
        <p className="mt-2 text-xs text-muted">
          {formatCount(run.funnel.candidates.removed_as_duplicates)} duplicates merged within{' '}
          {run.funnel.candidates.merged_within_m} m.
        </p>
      )}
    </section>
  )
}

function Rejections({
  run,
  rejected,
  reason,
  onReason,
  onSite,
}: {
  run: PlanningRun
  rejected: RejectedSite[] | undefined
  reason: string | null
  onReason: (code: string | null) => void
  onSite: (id: string) => void
}) {
  const byReason = Object.entries(run.funnel.rejected?.by_reason ?? {}).sort((a, b) => b[1] - a[1])
  const shown = (rejected ?? []).filter((r) => !reason || r.reason_codes.includes(reason))
  return (
    <section aria-label="Rejections">
      <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">
        Rejected · {formatCount(run.funnel.rejected?.total ?? 0)}
      </h2>
      <ul className="flex flex-col gap-1">
        {byReason.map(([code, n]) => (
          <li key={code}>
            <button
              type="button"
              aria-pressed={reason === code}
              onClick={() => onReason(reason === code ? null : code)}
              className={`flex w-full items-baseline gap-2 rounded px-1.5 py-1 text-left text-sm ${
                reason === code ? 'bg-ink text-paper' : 'hover:bg-line/50'
              }`}
            >
              <span className="font-mono text-xs">{code}</span>
              <span className="flex-1">{RULE_LABELS[code] ?? code}</span>
              <span className="tabular-nums">{formatCount(n)}</span>
            </button>
          </li>
        ))}
      </ul>
      {byReason.length > 0 && (
        <ul
          className="mt-3 flex max-h-80 flex-col gap-1 overflow-y-auto"
          aria-label="Rejected sites"
        >
          {shown.slice(0, 200).map((r) => (
            <li key={r.site_id}>
              <button
                type="button"
                onClick={() => onSite(r.site_id)}
                className="w-full rounded border border-line p-1.5 text-left text-xs hover:border-ink"
              >
                <div className="flex justify-between gap-2">
                  <span className="font-medium">{siteTitle(r)}</span>
                  <span className="font-mono">{r.reason_codes.join(' ')}</span>
                </div>
                <div className="text-muted">
                  {ORIGIN_LABELS[r.origin] ?? r.origin} ·{' '}
                  {r.reason_codes.map((c) => describeRule(r.reasons[c])).join(' / ')}
                </div>
              </button>
            </li>
          ))}
          {shown.length > 200 && (
            <li className="text-xs text-muted">
              Showing 200 of {formatCount(shown.length)}; filter by rule to narrow.
            </li>
          )}
        </ul>
      )}
    </section>
  )
}

function Legend({ plan = false }: { plan?: boolean }) {
  if (plan) {
    return (
      <div className="absolute bottom-8 left-3 rounded border border-line bg-panel/95 p-2 text-xs shadow">
        <div className="mb-1 font-semibold">Plan</div>
        {(
          [
            ['selected', 'selected / kept'],
            ['added', 'added by what-if'],
            ['removed', 'dropped by what-if'],
          ] as const
        ).map(([k, label]) => (
          <div key={k} className="flex items-center gap-2">
            <span
              className="inline-block h-3 w-3 rounded-full border-2 border-ink"
              style={{ background: SITE_COLORS[k] }}
            />
            {label}
          </div>
        ))}
        <div className="mt-1 max-w-48 text-muted">
          Coloured cells: which site serves each area. Candidates require field verification.
        </div>
      </div>
    )
  }
  return (
    <div className="absolute bottom-8 left-3 rounded border border-line bg-panel/95 p-2 text-xs shadow">
      <div className="mb-1 font-semibold">Candidate sites</div>
      <div className="flex items-center gap-2">
        <span
          className="inline-block h-2.5 w-16 rounded"
          style={{ background: `linear-gradient(to right, #c6dbef, ${SITE_COLORS.feasible})` }}
        />
        feasible, by score
      </div>
      <div className="flex items-center gap-2">
        <span
          className="inline-block h-3.5 w-3.5 rounded-full border-2 border-ink"
          style={{ background: SITE_COLORS.feasible }}
        />
        top N under the chosen profile
      </div>
      <div className="flex items-center gap-2">
        <span
          className="inline-block h-2.5 w-2.5 rounded-full"
          style={{ background: SITE_COLORS.rejected }}
        />
        rejected
      </div>
      <div className="mt-1 max-w-48 text-muted">
        Candidates require field verification before any decision.
      </div>
    </div>
  )
}

/** One planning run: live progress, funnel, rejections with evidence, and candidates on the map. */
export function RunPage({ runId }: { runId: string }) {
  const [evidence, setEvidence] = useState<EvidenceTarget>(null)
  const [reason, setReason] = useState<string | null>(null)
  const [chosenTab, setTab] = useState<Tab | null>(null)
  const [overlay, setOverlay] = useState<PlanOverlay>({ sites: null, cells: null })
  const onOverlay = useCallback((o: PlanOverlay) => setOverlay(o), [])
  const [profile, setProfile] = useState<string | null>(null)
  const [compare, setCompare] = useState<string[]>([])
  const [basemap, setBasemap] = useBasemap()

  const run = useQuery({
    queryKey: ['run', runId],
    queryFn: () => getJson<PlanningRun>(`/runs/${runId}`),
    refetchInterval: (q) => (q.state.data && FINISHED.has(q.state.data.status) ? false : 2000),
  })
  const finished = run.data ? FINISHED.has(run.data.status) : false
  useRunEvents(runId, finished)

  const sites = useQuery({
    queryKey: ['run-sites', runId],
    queryFn: () => getJson<FeatureCollection<Point, SiteProperties>>(`/runs/${runId}/sites`),
    enabled: run.data?.status === 'succeeded',
  })
  const rejected = useQuery({
    queryKey: ['run-rejected', runId],
    queryFn: () => getJson<RejectedSite[]>(`/runs/${runId}/rejected`),
    enabled: run.data?.status === 'succeeded',
  })
  const scoring = run.data?.scoring ?? null
  const hasPlan = !!run.data?.funnel.optimisation?.base_id
  const tabs = TABS.filter(([id]) =>
    id === 'plan' || id === 'strategies' || id === 'phasing'
      ? hasPlan
      : id === 'ranking'
        ? !!scoring
        : true,
  )
  const tab: Tab = chosenTab ?? (hasPlan ? 'plan' : scoring ? 'ranking' : 'funnel')
  const activeProfile = profile ?? scoring?.weight_profile ?? null
  const ranking = useQuery({
    queryKey: ['ranking', runId, activeProfile],
    queryFn: () => getJson<Ranking>(`/runs/${runId}/ranking?profile=${activeProfile}&limit=2000`),
    enabled: run.data?.status === 'succeeded' && !!activeProfile,
  })
  const showRanking = tab === 'ranking' && !!scoring
  const shownSites = useMemo(() => {
    if (tab === 'plan' || tab === 'strategies') return overlay.sites
    if (!sites.data) return null
    if (showRanking) {
      // Ranks follow the profile chosen here, not only the one the run stored.
      const byId = new Map(ranking.data?.sites.map((s) => [s.site_id, s]))
      return {
        ...sites.data,
        features: sites.data.features
          .filter((f) => f.properties.status === 'feasible')
          .map((f) => {
            const r = byId.get(f.properties.id)
            return {
              ...f,
              properties: {
                ...f.properties,
                rank: r?.rank ?? null,
                score_total: r?.score_total ?? null,
                top: r ? r.rank <= (ranking.data?.top_n ?? 0) : false,
              },
            }
          }),
      }
    }
    if (!reason) return sites.data
    return {
      ...sites.data,
      features: sites.data.features.filter((f) => f.properties.reason_codes.includes(reason)),
    }
  }, [sites.data, reason, showRanking, ranking.data, tab, overlay.sites])

  const selectedSiteId = evidence?.kind === 'site' ? evidence.siteId : null
  const toggleCompare = (id: string) =>
    setCompare((cur) => (cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]))
  const openSite = (id: string | null, optimisationId?: string) =>
    setEvidence(id ? { kind: 'site', runId, siteId: id, optimisationId } : null)

  if (run.isError) return <p className="p-6 text-sm text-warn">Couldn’t load this run.</p>
  const r = run.data

  return (
    <div className="grid h-full grid-cols-[360px_1fr] overflow-hidden">
      <div className="flex flex-col gap-5 overflow-y-auto border-r border-line bg-paper p-4">
        <div>
          <div className="flex justify-between text-xs">
            <Link to="/plans" className="underline">
              ← All runs
            </Link>
            <span className="flex gap-3">
              {hasPlan && (
                <Link to="/runs/$runId/grid" params={{ runId }} className="underline">
                  Grid impact
                </Link>
              )}
              <Link to="/assistant" search={{ runId }} className="underline">
                Ask the assistant
              </Link>
            </span>
          </div>
          <h1 className="mt-1 text-lg font-semibold">{r?.scenario.name ?? 'Loading…'}</h1>
          {r && (
            <p className="text-xs text-muted">
              {r.scenario.target_year} · {r.scenario.adoption_case} adoption ·{' '}
              {r.scenario.charger_classes.join(', ')}
              {r.scenario.user_sites.length > 0 &&
                ` · ${r.scenario.user_sites.length} of your sites`}
            </p>
          )}
        </div>
        {r && (
          <>
            <Steps run={r} />
            {r.error && <p className="text-sm text-warn">Run failed: {r.error}</p>}
            {(r.demand_synthetic || r.placeholders_in_use.length > 0) && (
              <div className="flex flex-col gap-1 rounded border border-dashed border-synthetic p-2 text-xs text-synthetic">
                {r.demand_synthetic && (
                  <p>
                    Demand inputs are synthetic (no confidence), so gap-cell candidates and F03 are
                    illustrative.
                  </p>
                )}
                {r.placeholders_in_use.length > 0 && (
                  <details>
                    <summary className="cursor-pointer">
                      {r.placeholders_in_use.length} illustrative assumptions in use
                    </summary>
                    <p className="mt-1">{r.placeholders_in_use.join(', ')}</p>
                  </details>
                )}
              </div>
            )}
            {r.status === 'succeeded' && (
              <>
                {tabs.length > 1 && (
                  <div role="tablist" aria-label="Run view" className="flex flex-wrap gap-1">
                    {tabs.map(([id, label]) => (
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
                {hasPlan && <ExportButtons runId={runId} />}
                {tab === 'phasing' ? (
                  <PhasingPanel
                    runId={runId}
                    baseBudget={r.scenario.budget_inr ?? 0}
                    onSite={(id) => openSite(id)}
                  />
                ) : tab === 'plan' || tab === 'strategies' ? (
                  <PlanPanel run={r} onSite={openSite} onOverlay={onOverlay} />
                ) : showRanking && scoring && activeProfile ? (
                  <RankingPanel
                    ranking={ranking.data}
                    scoring={scoring}
                    profile={activeProfile}
                    onProfile={setProfile}
                    shown={scoring.shortlist}
                    compare={compare}
                    onToggleCompare={toggleCompare}
                    onSite={openSite}
                  />
                ) : (
                  <>
                    <FunnelBars run={r} />
                    <Rejections
                      run={r}
                      rejected={rejected.data}
                      reason={reason}
                      onReason={setReason}
                      onSite={openSite}
                    />
                  </>
                )}
              </>
            )}
          </>
        )}
      </div>
      <div className="flex min-h-0 flex-col">
        <div className="relative min-h-0 flex-1">
          {basemap === 'google' && r ? (
            <GoogleLayersMap
              sites={shownSites}
              selectedSiteId={selectedSiteId}
              onSelectSite={openSite}
              cellFill={tab === 'plan' ? overlay.cells : null}
              center={
                r?.scenario.region_id === 'mumbai_pune' ? { lat: 18.85, lng: 73.3 } : undefined
              }
              zoom={r?.scenario.region_id === 'mumbai_pune' ? 9 : 11}
            />
          ) : (
            <OpenMapCanvas
              sites={shownSites}
              selectedSiteId={selectedSiteId}
              onSelectSite={openSite}
              onSelectStation={(id) => setEvidence(id ? { kind: 'station', stationId: id } : null)}
              catchmentUrl={
                selectedSiteId && scoring
                  ? `/runs/${runId}/sites/${selectedSiteId}/catchment`
                  : null
              }
              cellFill={tab === 'plan' ? overlay.cells : null}
            />
          )}
          <BasemapToggle value={basemap} onChange={setBasemap} />
          <Legend plan={tab === 'plan'} />
          {tab === 'strategies' && (
            <div className="absolute inset-0 z-20 bg-paper">
              <StrategiesPanel runId={runId} />
            </div>
          )}
          <EvidenceDrawer target={evidence} onClose={() => setEvidence(null)} />
        </div>
        {/* Below the map, not over it: overlays on the WebGL canvas blanked tiles. */}
        {compare.length >= 2 && (
          <ComparePanel
            runId={runId}
            ids={compare}
            onClose={() => setCompare([])}
            onSite={openSite}
          />
        )}
      </div>
    </div>
  )
}

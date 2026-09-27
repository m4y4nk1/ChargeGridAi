import { ConfidenceBadge } from '../../components/badges'
import { SUB_SCORES, type IndexKey, type SiteScoring, type SubScores } from '../../lib/api'
import { formatCount } from '../../lib/format'
import {
  contributions,
  direction,
  DIRECTION_GLYPH,
  INDEX_LABELS,
  INDEX_UNAVAILABLE,
  robustnessBadge,
  SUB_SCORE_COLORS,
  SUB_SCORE_HELP,
  SUB_SCORE_LABELS,
} from '../../lib/scoring'

/** Stacked bar: each segment is one sub-score's weighted points out of 100. */
export function ContributionBar({ sub, weights }: { sub: SubScores; weights: SubScores }) {
  const parts = contributions(sub, weights)
  return (
    <div
      className="flex h-2 w-full overflow-hidden rounded bg-line"
      role="img"
      aria-label={parts.map((p) => `${SUB_SCORE_LABELS[p.key]} ${p.points.toFixed(1)}`).join(', ')}
    >
      {parts.map((p) => (
        <div
          key={p.key}
          style={{ width: `${p.points}%`, background: SUB_SCORE_COLORS[p.key] }}
          title={`${SUB_SCORE_LABELS[p.key]}: ${p.points.toFixed(1)} points`}
        />
      ))}
    </div>
  )
}

export function ContributionLegend({ weights }: { weights: SubScores }) {
  return (
    <div className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted">
      {SUB_SCORES.filter((k) => weights[k] > 0).map((k) => (
        <span key={k} className="flex items-center gap-1">
          <span
            className="inline-block h-2 w-2 rounded-sm"
            style={{ background: SUB_SCORE_COLORS[k] }}
          />
          {SUB_SCORE_LABELS[k]} {Math.round(weights[k] * 100)}%
        </span>
      ))}
    </div>
  )
}

const TONE_STYLE = {
  robust: 'border border-ink text-ink',
  sensitive: 'border border-warn text-warn',
  fragile: 'border border-dashed border-warn text-warn',
} as const

export function RobustnessBadge({ share, topN }: { share: number | null; topN: number }) {
  const b = robustnessBadge(share, topN)
  if (!b) return null
  return (
    <span
      className={`inline-flex whitespace-nowrap rounded px-1.5 py-0.5 text-[11px] leading-none ${TONE_STYLE[b.tone]}`}
      title="Share of 1,000 random ±20% variations of the weights in which this site stays in the top N"
    >
      {b.text}
    </span>
  )
}

function IndexRow({ k, value }: { k: IndexKey; value: number | null }) {
  const dir = direction(value, 100)
  const width = value === null ? 0 : Math.min(value, 300) / 3
  return (
    <li className="text-sm">
      <div className="flex justify-between gap-2">
        <span>{INDEX_LABELS[k]}</span>
        <span className="tabular-nums">
          {value === null ? (
            <span className="text-xs text-muted">{INDEX_UNAVAILABLE[k] ?? 'n/a'}</span>
          ) : (
            <>
              {dir && <span aria-label={dir}>{DIRECTION_GLYPH[dir]} </span>}
              {Math.round(value)}
            </>
          )}
        </span>
      </div>
      {value !== null && (
        <div className="relative mt-0.5 h-1.5 rounded bg-line">
          <div className="h-1.5 rounded bg-ink" style={{ width: `${width}%` }} />
          {/* Benchmark = 100 sits at a third of the scale (0-300). */}
          <div className="absolute top-[-2px] h-2.5 w-px bg-warn" style={{ left: '33.3%' }} />
        </div>
      )}
    </li>
  )
}

/** Site drawer section: total, rank, robustness, sub-scores with raw inputs, indices, POIs. */
export function SiteScoreBreakdown({ scoring }: { scoring: SiteScoring }) {
  if (scoring.unscored) {
    return <p className="text-sm text-warn">Not scored: {scoring.unscored}</p>
  }
  const run = scoring.run
  if (!run || scoring.score_total === null) return null
  const raw = scoring.raw ?? {}
  const rawText: Record<string, string> = {
    demand: `${formatCount(Math.round(raw.demand_kwh_per_day as number))} kWh/day in catchment`,
    accessibility: `${String(raw.nearest_road_class ?? 'unknown')} road · ${formatCount(raw.catchment_population as number)} people`,
    traffic: `${formatCount(raw.major_road_m_within_radius as number)} m of major road within 1 km`,
    grid:
      raw.nearest_substation_m === null
        ? 'no substation found'
        : `${formatCount(Math.round(raw.nearest_substation_m as number))} m to substation`,
    land: `${String(raw.host_type ?? '')
      .replaceAll('_', ' ')
      .toLowerCase()}${raw.parking_nearby ? ' + parking nearby' : ''}`,
    future_growth:
      raw.demand_cagr === null
        ? 'no demand to grow from'
        : `${((raw.demand_cagr as number) * 100).toFixed(1)}%/yr, ${(raw.growth_window as number[]).join('–')}`,
    competition_gap: `${formatCount(Math.round(raw.existing_supply_kwh_per_day as number))} kWh/day existing supply`,
    equity: `${Math.round((raw.underserved_population_share as number) * 100)}% of people in underserved cells`,
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-2xl font-semibold tabular-nums">
          {scoring.score_total.toFixed(1)}
        </span>
        <span className="text-sm text-muted">
          rank {scoring.rank} · {run.weight_profile.replaceAll('_', ' ')}
        </span>
        <RobustnessBadge share={scoring.robustness} topN={run.top_n} />
        <ConfidenceBadge value={run.total_confidence} />
      </div>
      <ContributionBar sub={scoring.sub_scores} weights={run.weights} />

      <section>
        <h4 className="text-xs font-semibold uppercase tracking-wide text-muted">
          Sub-scores (percentile among feasible candidates)
        </h4>
        <ul className="mt-1 flex flex-col gap-2">
          {SUB_SCORES.map((k) => (
            <li key={k} className="text-sm" title={SUB_SCORE_HELP[k]}>
              <div className="flex items-center justify-between gap-2">
                <span className="flex items-center gap-1.5">
                  <span
                    className="inline-block h-2 w-2 rounded-sm"
                    style={{ background: SUB_SCORE_COLORS[k] }}
                  />
                  {SUB_SCORE_LABELS[k]}
                  <span className="text-xs text-muted">×{Math.round(run.weights[k] * 100)}%</span>
                </span>
                <span className="tabular-nums">{Math.round(scoring.sub_scores[k])}</span>
              </div>
              <div className="mt-0.5 h-1.5 rounded bg-line">
                <div
                  className="h-1.5 rounded"
                  style={{ width: `${scoring.sub_scores[k]}%`, background: SUB_SCORE_COLORS[k] }}
                />
              </div>
              <div className="mt-0.5 flex items-center justify-between gap-2 text-xs text-muted">
                <span>{rawText[k]}</span>
                <ConfidenceBadge value={run.confidence[k]} />
              </div>
              {run.uninformative[k] && (
                <div className="text-xs text-warn">Uninformative: {run.uninformative[k]}</div>
              )}
            </li>
          ))}
        </ul>
      </section>

      {scoring.catchment && scoring.indices && (
        <section>
          <h4 className="text-xs font-semibold uppercase tracking-wide text-muted">
            {scoring.catchment.minutes}-minute drive catchment vs region (= 100)
          </h4>
          <p className="mt-1 text-xs text-muted">
            {formatCount(scoring.catchment.population)} people ·{' '}
            {formatCount(Math.round(scoring.catchment.evs))} EVs ·{' '}
            {scoring.catchment.existing_stations} existing stations (
            {scoring.catchment.existing_charge_points} charge points)
          </p>
          <ul className="mt-2 flex flex-col gap-1.5">
            {(Object.keys(INDEX_LABELS) as IndexKey[]).map((k) => (
              <IndexRow key={k} k={k} value={scoring.indices?.[k] ?? null} />
            ))}
          </ul>
        </section>
      )}

      {scoring.poi_counts && Object.keys(scoring.poi_counts).length > 0 && (
        <section>
          <h4 className="text-xs font-semibold uppercase tracking-wide text-muted">
            Places in the catchment (OSM)
          </h4>
          <ul className="mt-1 grid grid-cols-2 gap-x-4 text-sm">
            {Object.entries(scoring.poi_counts)
              .sort((a, b) => b[1] - a[1])
              .map(([cat, n]) => (
                <li key={cat} className="flex justify-between">
                  <span>{cat.replaceAll('_', ' ').toLowerCase()}</span>
                  <span className="tabular-nums">{formatCount(n)}</span>
                </li>
              ))}
          </ul>
        </section>
      )}
      <p className="text-xs text-muted">
        Revenue, sizing and finance: see the Sizing and Finance tabs of sites in the plan.
      </p>
    </div>
  )
}

import { useQueries, useQuery } from '@tanstack/react-query'
import {
  getJson,
  SUB_SCORES,
  type IndexKey,
  type SiteDetail,
  type SiteEconomics,
} from '../../lib/api'
import { formatCount, formatLakh } from '../../lib/format'
import { siteTitle } from '../../lib/planning'
import {
  direction,
  DIRECTION_GLYPH,
  INDEX_LABELS,
  INDEX_UNAVAILABLE,
  SUB_SCORE_LABELS,
} from '../../lib/scoring'

/** Steady-state P50 monthly revenue, for sites in the run's plan (Phase 7 finance). */
function MonthlyRevenueCell({ site }: { site: SiteDetail }) {
  const optId = site.plan?.selected ? site.plan.optimisation_id : null
  const econ = useQuery({
    queryKey: ['economics', optId, site.id, false],
    queryFn: () =>
      getJson<SiteEconomics>(`/optimisations/${optId}/sites/${site.id}/economics?subsidy=false`),
    enabled: !!optId,
    staleTime: Infinity,
  })
  const text = !optId
    ? 'not in plan'
    : econ.data
      ? formatLakh(econ.data.finance.monte_carlo.monthly_revenue_steady.p50)
      : '…'
  return (
    <td className={`px-2 py-1 text-right tabular-nums ${optId ? '' : 'text-xs text-muted'}`}>
      {text}
    </td>
  )
}

function Cell({ value, benchmark }: { value: number | null | undefined; benchmark: number }) {
  if (value === null || value === undefined) return <td className="px-2 py-1 text-muted">—</td>
  const dir = direction(value, benchmark)
  return (
    <td className="px-2 py-1 text-right tabular-nums">
      {dir && (
        <span
          aria-label={dir}
          className={dir === 'above' ? 'text-ink' : dir === 'below' ? 'text-warn' : 'text-muted'}
        >
          {DIRECTION_GLYPH[dir]}{' '}
        </span>
      )}
      {Math.round(value)}
    </td>
  )
}

/**
 * Comparison table (module B): each factor per site, with a direction against its
 * reference: sub-scores against the median candidate (50), catchment indices against
 * the region (100).
 */
export function ComparePanel({
  runId,
  ids,
  onClose,
  onSite,
}: {
  runId: string
  ids: string[]
  onClose: () => void
  onSite: (id: string) => void
}) {
  const details = useQueries({
    queries: ids.map((id) => ({
      queryKey: ['site', runId, id],
      queryFn: () => getJson<SiteDetail>(`/runs/${runId}/sites/${id}`),
    })),
  })
  const sites = details.map((d) => d.data).filter((d): d is SiteDetail => !!d)
  if (ids.length < 2) return null

  return (
    <section
      aria-label="Compare sites"
      className="max-h-[45%] shrink-0 overflow-auto border-t border-line bg-panel"
    >
      <div className="sticky top-0 flex items-center justify-between border-b border-line bg-panel px-3 py-2">
        <h2 className="text-sm font-semibold">Compare {ids.length} sites</h2>
        <span className="text-xs text-muted">
          ▲ above / ▼ below the reference (±10% counts as level ●)
        </span>
        <button type="button" onClick={onClose} className="text-sm underline">
          Close
        </button>
      </div>
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs text-muted">
            <th className="px-2 py-1">Factor</th>
            <th className="px-2 py-1 text-right">Reference</th>
            {sites.map((s) => (
              <th key={s.id} className="px-2 py-1 text-right">
                <button type="button" className="underline" onClick={() => onSite(s.id)}>
                  {siteTitle(s)}
                </button>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          <tr className="border-t border-line font-medium">
            <td className="px-2 py-1">Total score</td>
            <td className="px-2 py-1 text-right text-muted">—</td>
            {sites.map((s) => (
              <td key={s.id} className="px-2 py-1 text-right tabular-nums">
                {s.scoring?.score_total?.toFixed(1) ?? '—'}{' '}
                <span className="text-xs text-muted">#{s.scoring?.rank ?? '—'}</span>
              </td>
            ))}
          </tr>
          {SUB_SCORES.map((k) => (
            <tr key={k} className="border-t border-line">
              <td className="px-2 py-1">{SUB_SCORE_LABELS[k]}</td>
              <td className="px-2 py-1 text-right text-muted">50</td>
              {sites.map((s) => (
                <Cell key={s.id} value={s.scoring?.sub_scores[k]} benchmark={50} />
              ))}
            </tr>
          ))}
          {(Object.keys(INDEX_LABELS) as IndexKey[]).map((k) => (
            <tr key={k} className="border-t border-line">
              <td className="px-2 py-1">
                {INDEX_LABELS[k]}
                {INDEX_UNAVAILABLE[k] && (
                  <span className="text-xs text-muted"> ({INDEX_UNAVAILABLE[k]})</span>
                )}
              </td>
              <td className="px-2 py-1 text-right text-muted">100</td>
              {sites.map((s) => (
                <Cell key={s.id} value={s.scoring?.indices?.[k]} benchmark={100} />
              ))}
            </tr>
          ))}
          <tr className="border-t border-line">
            <td className="px-2 py-1">Existing charge points in catchment</td>
            <td className="px-2 py-1 text-right text-muted">—</td>
            {sites.map((s) => (
              <td key={s.id} className="px-2 py-1 text-right tabular-nums">
                {formatCount(s.scoring?.catchment?.existing_charge_points)}
              </td>
            ))}
          </tr>
          <tr className="border-t border-line">
            <td className="px-2 py-1">P50 monthly revenue</td>
            <td className="px-2 py-1 text-right text-muted">—</td>
            {sites.map((s) => (
              <MonthlyRevenueCell key={s.id} site={s} />
            ))}
          </tr>
        </tbody>
      </table>
    </section>
  )
}

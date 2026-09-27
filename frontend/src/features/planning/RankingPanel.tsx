import { useQuery } from '@tanstack/react-query'
import { getJson, type Ranking, type ScoringSummary, type WeightProfile } from '../../lib/api'
import { siteTitle } from '../../lib/planning'
import { SUB_SCORE_LABELS } from '../../lib/scoring'
import { ContributionBar, ContributionLegend, RobustnessBadge } from './ScoreParts'

export const MAX_COMPARE = 4

/** Site Explorer list (module B): ranked candidates, profile switch, compare picks. */
export function RankingPanel({
  ranking,
  scoring,
  profile,
  onProfile,
  shown,
  compare,
  onToggleCompare,
  onSite,
}: {
  ranking: Ranking | undefined
  scoring: ScoringSummary
  profile: string
  onProfile: (name: string) => void
  shown: number
  compare: string[]
  onToggleCompare: (id: string) => void
  onSite: (id: string) => void
}) {
  const profiles = useQuery({
    queryKey: ['weight-profiles'],
    queryFn: () => getJson<WeightProfile[]>('/weight-profiles'),
  })
  const uninformative = Object.entries(ranking?.uninformative ?? scoring.uninformative)

  return (
    <section aria-label="Ranking" className="flex flex-col gap-3">
      <label className="flex flex-col text-xs text-muted">
        Weight profile
        <select
          className="mt-1 rounded border border-line bg-panel px-2 py-1 text-sm text-ink"
          value={profile}
          onChange={(e) => onProfile(e.target.value)}
        >
          {profiles.data?.map((p) => (
            <option key={p.name} value={p.name}>
              {p.label}
              {p.name === scoring.weight_profile ? ' (this run)' : ''}
            </option>
          ))}
        </select>
      </label>
      {ranking && <ContributionLegend weights={ranking.weights} />}
      {uninformative.length > 0 && (
        <p className="text-xs text-warn">
          {uninformative
            .map(([k, why]) => `${SUB_SCORE_LABELS[k as keyof typeof SUB_SCORE_LABELS]}: ${why}`)
            .join('; ')}
          . It scores every site 50, so it can’t change the order.
        </p>
      )}
      <p className="text-xs text-muted">
        Showing the shortlist ({shown} of {ranking?.scored ?? '…'} scored). Tick up to {MAX_COMPARE}{' '}
        to compare.
      </p>
      <ol className="flex flex-col gap-1" aria-label="Ranked sites">
        {ranking?.sites.slice(0, shown).map((s) => {
          const picked = compare.includes(s.site_id)
          return (
            <li
              key={s.site_id}
              className={`flex gap-2 rounded border p-1.5 ${
                s.rank <= ranking.top_n ? 'border-ink/40' : 'border-line'
              }`}
            >
              <input
                type="checkbox"
                aria-label={`Compare ${siteTitle(s)}`}
                checked={picked}
                disabled={!picked && compare.length >= MAX_COMPARE}
                onChange={() => onToggleCompare(s.site_id)}
                className="mt-1"
              />
              <button
                type="button"
                onClick={() => onSite(s.site_id)}
                className="flex min-w-0 flex-1 flex-col gap-1 text-left"
              >
                <div className="flex items-baseline gap-2 text-sm">
                  <span className="w-6 text-right text-xs text-muted tabular-nums">{s.rank}</span>
                  <span className="min-w-0 flex-1 truncate font-medium">{siteTitle(s)}</span>
                  <span className="tabular-nums">{s.score_total.toFixed(1)}</span>
                </div>
                <ContributionBar sub={s.sub_scores} weights={ranking.weights} />
                <div className="flex flex-wrap items-center gap-1">
                  <span className="text-[11px] text-muted">
                    {s.host_type?.replaceAll('_', ' ').toLowerCase()}
                  </span>
                  <RobustnessBadge share={s.robustness} topN={ranking.top_n} />
                </div>
              </button>
            </li>
          )
        })}
      </ol>
    </section>
  )
}

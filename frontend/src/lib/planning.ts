/** Pure helpers for planning runs: rule labels, funnel stages, user-site parsing. */
import type { Funnel, OptimisationSummary, RuleDetail, UserSite } from './api'
import { formatCount } from './format'

// Mirrors config/policy/rules.yaml descriptions (Section 9.5).
export const RULE_LABELS: Record<string, string> = {
  F01: 'Restricted land (water, forest, military, protected, airport)',
  F02: 'Too far from a drivable road',
  F03: 'Existing DC station already serves this area',
  F04: 'Too far from grid supply',
  F05: 'Residential interior street, no arterial access (DC)',
  F06: 'Hazard overlay (flood zones)',
  F07: 'Duplicate of a selected site',
}

export const ORIGIN_LABELS: Record<string, string> = {
  poi: 'Host POI',
  corridor: 'Highway corridor',
  gap_cell: 'Demand gap cell',
  user: 'Your site',
}

/** Display name for a candidate: its host's name, else its host type in words. */
export function siteTitle(site: { name: string | null; host_type: string | null }): string {
  if (site.name) return site.name
  if (!site.host_type || site.host_type === 'ROADSIDE') return 'Roadside point'
  const words = site.host_type.replaceAll('_', ' ').toLowerCase()
  return `Unnamed ${words}`
}

export const OUTCOME_LABELS: Record<RuleDetail['outcome'], string> = {
  pass: 'pass',
  fail: 'fail',
  not_applied: 'not applied',
  disabled: 'disabled',
  deferred: 'deferred',
}

/** One sentence of evidence for a rule outcome, built only from what the engine measured. */
export function describeRule(detail: RuleDetail): string {
  const { measured, threshold, unit, note } = detail
  if (Array.isArray(measured)) {
    const hits = measured as { class?: string; name?: string | null }[]
    if (hits.length === 0) return note ?? 'No restricted land at the site'
    return hits.map((h) => (h.name ? `${h.class}: ${h.name}` : `${h.class}`)).join('; ')
  }
  const parts: string[] = []
  if (typeof measured === 'number') {
    const limit =
      typeof threshold === 'number' ? ` (limit ${formatCount(threshold)} ${unit ?? ''})` : ''
    parts.push(`measured ${formatCount(measured)} ${unit ?? ''}${limit}`.replace(/\s+\)/, ')'))
  } else if (measured === null && detail.outcome !== 'disabled' && detail.outcome !== 'deferred') {
    parts.push('nothing found within search radius')
  }
  if (note) parts.push(note)
  for (const [key, value] of Object.entries(detail)) {
    if (['outcome', 'measured', 'threshold', 'unit', 'note'].includes(key)) continue
    if (typeof value === 'number' || typeof value === 'string')
      parts.push(
        `${key.replaceAll('_', ' ')} ${typeof value === 'number' ? formatCount(value) : value}`,
      )
  }
  return parts.join(' · ') || OUTCOME_LABELS[detail.outcome]
}

export interface FunnelStage {
  key: 'theoretical' | 'candidates' | 'feasible' | 'shortlisted' | 'selected'
  label: string
  value: number | null
  /** Why the stage has no value yet. */
  pending?: string
}

export function funnelStages(f: Funnel): FunnelStage[] {
  return [
    { key: 'theoretical', label: 'Theoretical', value: f.theoretical?.total ?? null },
    { key: 'candidates', label: 'Candidates (deduped)', value: f.candidates?.total ?? null },
    { key: 'feasible', label: 'Feasible', value: f.feasible?.total ?? null },
    {
      key: 'shortlisted',
      label: 'Shortlisted',
      value: f.shortlisted ?? null,
      pending: f.shortlisted == null ? 'scoring arrives in Phase 5' : undefined,
    },
    {
      key: 'selected',
      label: 'Selected',
      value: f.selected ?? null,
      pending: f.selected == null ? 'optimisation arrives in Phase 6' : undefined,
    },
  ]
}

export type ParsedSites = { sites: UserSite[]; errors: string[] }

/**
 * Parse one site per line: `lat, lng[, name]`. Blank lines and `#` comments are skipped.
 * Region bounds are validated by the API, which knows the modelled region.
 */
export function parseUserSites(text: string): ParsedSites {
  const sites: UserSite[] = []
  const errors: string[] = []
  text.split('\n').forEach((raw, i) => {
    const line = raw.trim()
    if (!line || line.startsWith('#')) return
    const [latS, lngS, ...rest] = line.split(',').map((p) => p.trim())
    const lat = Number(latS)
    const lng = Number(lngS)
    if (!latS || !lngS || !Number.isFinite(lat) || !Number.isFinite(lng)) {
      errors.push(`Line ${i + 1}: expected "lat, lng, name"`)
      return
    }
    if (Math.abs(lat) > 90 || Math.abs(lng) > 180) {
      errors.push(`Line ${i + 1}: coordinates out of range`)
      return
    }
    const name = rest.join(', ').trim()
    sites.push({ lat, lng, name: name || null })
  })
  return { sites, errors }
}

/** Groups of strategies that produced the same plan (same sites and cost). */
export function identicalPlans(opts: OptimisationSummary[]): string[][] {
  const groups = new Map<string, string[]>()
  for (const o of opts) {
    const key = `${o.kpis.cost_inr}|${o.kpis.n_sites}|${o.kpis.top_sites.map((s) => s.site_id).join(',')}`
    groups.set(key, [...(groups.get(key) ?? []), o.label])
  }
  return [...groups.values()].filter((g) => g.length > 1)
}

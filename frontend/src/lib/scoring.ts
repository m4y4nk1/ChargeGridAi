/** Pure helpers for scores: labels, colours, contributions, benchmark directions. */
import { SUB_SCORES, type IndexKey, type SubScore, type SubScores } from './api'

export const SUB_SCORE_LABELS: Record<SubScore, string> = {
  demand: 'Demand',
  accessibility: 'Accessibility',
  traffic: 'Traffic (proxy)',
  grid: 'Grid (proxy)',
  land: 'Land / host',
  future_growth: 'Growth',
  competition_gap: 'Competition gap',
  equity: 'Equity (access)',
}

/** What each sub-score measures, in one line (docs/methodology/scoring.md). */
export const SUB_SCORE_HELP: Record<SubScore, string> = {
  demand: 'Public charging demand (kWh/day, P50) inside the drive-time catchment',
  accessibility: 'Nearest road class and population inside the catchment',
  traffic: 'Length of major road within 1 km. No traffic counts yet, so a proxy',
  grid: 'Distance to the nearest substation. Proximity only, not capacity',
  land: 'How well the host type suits a charger, plus parking nearby',
  future_growth: 'Growth rate of catchment demand after the target year',
  competition_gap: 'Share of catchment demand existing chargers do not cover',
  equity: 'Population share of the catchment in underserved cells (no income data yet)',
}

// Okabe-Ito: distinguishable under the common colour-vision deficiencies.
export const SUB_SCORE_COLORS: Record<SubScore, string> = {
  demand: '#0072B2',
  accessibility: '#56B4E9',
  traffic: '#E69F00',
  grid: '#D55E00',
  land: '#009E73',
  future_growth: '#CC79A7',
  competition_gap: '#F0E442',
  equity: '#999999',
}

export const INDEX_LABELS: Record<IndexKey, string> = {
  population_density: 'Population density',
  evs_per_1000_people: 'EVs per 1,000 people',
  demand_density: 'Charging demand density',
  charge_points_per_1000_evs: 'Existing charge points per 1,000 EVs',
  income: 'Income',
  home_charging_access: 'Home-charging access',
}

/** Why an index has no value (never estimated without a source). */
export const INDEX_UNAVAILABLE: Partial<Record<IndexKey, string>> = {
  income: 'no income source ingested',
  home_charging_access: 'no housing-type source ingested',
}

export interface Contribution {
  key: SubScore
  /** Points this sub-score adds to the 0-100 total. */
  points: number
}

/** Weighted contribution of each sub-score to the total; zero-weight ones omitted. */
export function contributions(sub: SubScores, weights: SubScores): Contribution[] {
  return SUB_SCORES.filter((k) => weights[k] > 0).map((k) => ({
    key: k,
    points: weights[k] * sub[k],
  }))
}

export type Direction = 'above' | 'below' | 'near'

/** Direction against a benchmark; within ±band% counts as "near". */
export function direction(value: number | null, benchmark: number, band = 10): Direction | null {
  if (value === null || !Number.isFinite(value)) return null
  if (value > benchmark * (1 + band / 100)) return 'above'
  if (value < benchmark * (1 - band / 100)) return 'below'
  return 'near'
}

export const DIRECTION_GLYPH: Record<Direction, string> = { above: '▲', below: '▼', near: '●' }

/** "Stays in the top 20 in 87% of weight variations" → badge text and tone. */
export function robustnessBadge(share: number | null, topN: number) {
  if (share === null) return null
  const pct = Math.round(share * 100)
  const tone = share >= 0.8 ? 'robust' : share >= 0.4 ? 'sensitive' : 'fragile'
  return { pct, tone, text: `Top ${topN} in ${pct}% of weight variations` } as const
}

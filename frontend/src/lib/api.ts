/**
 * Typed fetchers for the REST API (Section 11). Hand-written for now; these
 * move to an orval-generated client once the OpenAPI surface settles.
 */
import type { LicenseClass } from '../map/licenseGuard'
import { authHeaders, getToken } from './auth'

export const API_URL: string = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

export type Confidence = 'HIGH' | 'MEDIUM' | 'LOW' | 'NONE'

/** fetch against the API with the signed-in user's token; a 401 asks the app to sign in. */
export async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const response = await fetch(`${API_URL}/api/v1${path}`, {
    ...init,
    headers: { ...authHeaders(), ...(init.headers as Record<string, string> | undefined) },
  })
  if (response.status === 401) window.dispatchEvent(new Event('chargegrid-signin-required'))
  return response
}

/** URL for a server-sent event stream (EventSource can't send headers). */
export function streamUrl(path: string): string {
  const token = getToken()
  const sep = path.includes('?') ? '&' : '?'
  return `${API_URL}/api/v1${path}${token ? `${sep}access_token=${encodeURIComponent(token)}` : ''}`
}

export async function getJson<T>(path: string): Promise<T> {
  const response = await apiFetch(path)
  if (!response.ok) throw new Error(`${path} failed: ${response.status}`)
  return (await response.json()) as T
}

/** Download a file from the API (exports), keeping the auth header. */
export async function downloadFile(path: string, fallbackName: string): Promise<void> {
  const response = await apiFetch(path)
  if (!response.ok) throw new Error(`${path} failed: ${response.status}`)
  const disposition = response.headers.get('Content-Disposition') ?? ''
  const name = /filename="([^"]+)"/.exec(disposition)?.[1] ?? fallbackName
  const url = URL.createObjectURL(await response.blob())
  const a = document.createElement('a')
  a.href = url
  a.download = name
  a.click()
  URL.revokeObjectURL(url)
}

export async function putJson<T>(path: string, body: unknown): Promise<T> {
  return sendJson<T>('PUT', path, body)
}

export async function postJson<T>(path: string, body: unknown): Promise<T> {
  return sendJson<T>('POST', path, body)
}

async function sendJson<T>(method: string, path: string, body: unknown): Promise<T> {
  const response = await apiFetch(path, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!response.ok) {
    // FastAPI validation errors carry a list of {loc, msg}; surface the messages.
    const detail = (await response.json().catch(() => null)) as { detail?: unknown } | null
    const msg = Array.isArray(detail?.detail)
      ? (detail.detail as { msg: string }[]).map((d) => d.msg).join('; ')
      : typeof detail?.detail === 'string'
        ? detail.detail
        : `${response.status}`
    throw new Error(`${path} failed: ${msg}`)
  }
  return (await response.json()) as T
}

export interface StationProperties {
  id: string
  name: string | null
  operator: string | null
  license_class: LicenseClass
  confidence: Confidence
  charge_points: number
  max_kw: number | null
  power_band: string
  connectors: string[]
  sources: string[]
  has_conflicts: boolean
}

export interface StationDetail {
  id: string
  name: string | null
  operator: string | null
  lat: number
  lng: number
  access: string | null
  status: string | null
  opening_hours: string | null
  license_class: LicenseClass
  confidence: Confidence
  evses: {
    max_kw: number | null
    current: string | null
    count: number
    power_band: string
    connectors: { standard: string; max_kw: number | null }[]
  }[]
  sources: {
    source_id: string
    source_name: string
    license_class: LicenseClass
    attribution: string | null
    source_record_id: string
    match_score: number
    retrieved_at: string | null
  }[]
  conflicts: { field: string; values: Record<string, unknown> }[]
}

export interface CountRow {
  key: string
  stations: number
  charge_points: number
  connectors: number
}

export interface StationStats {
  region_id: number | null
  stations: number
  charge_points: number
  connectors: number
  by_operator: CountRow[]
  by_power_band: CountRow[]
  by_connector: CountRow[]
  evidence_ids: string[]
  note: string
}

export interface Snapshot {
  id: string
  retrieved_at: string
  period_start: string | null
  period_end: string | null
  granularity: string | null
  row_count: number | null
  stored: boolean
  checksum: string | null
  quality_report: Record<string, unknown> | null
}

export interface DataSource {
  id: string
  name: string
  license_class: LicenseClass
  license_text: string | null
  attribution: string | null
  terms_url: string | null
  refresh_cadence: string | null
  /** Ingested: fresh/stale/never/n/a. Live (fetched on use, never stored): live/off/not_configured. */
  freshness: 'fresh' | 'stale' | 'never' | 'n/a' | 'live' | 'off' | 'not_configured'
  access?: 'ingested' | 'live'
  usage?: { calls_this_month: number; cost_inr_this_month: number; last_used: string | null } | null
  /** Latest snapshot of each dataset from this source, newest first. */
  datasets: Snapshot[]
}

export interface RegionSummary {
  id: number
  name: string | null
  level: string
  admin_level: number | null
}

export interface EvidenceItem {
  id: string
  metric: string
  value_num: number | null
  unit: string | null
  method: string | null
  confidence: Confidence
  created_at: string
  sources: {
    snapshot_id: string
    source_id: string
    source_name: string
    license_class: LicenseClass
    attribution: string | null
    retrieved_at: string
  }[]
}

export interface Band {
  p10: number
  p50: number
  p90: number
}

export interface DemandRun {
  run_id: string
  evidence_id: string
  synthetic_registrations: boolean
  registration_sources: string[]
  observed_window: [string, string]
  scenarios: Record<string, number>
  placeholders_in_use: string[]
  allocation_status: string | null
  rto_catchments: Record<string, { boundaries: number[]; status?: string }>
  stations_with_assumed_count: number
  charge_points_with_assumed_kw: number
}

export interface AreaKpis {
  area: { id: number; name: string; level: string; area_km2: number; coverage_share: number }
  year: number
  scenario: string
  population: number
  population_year: number | null
  current_evs: number
  current_evs_as_of: string
  current_evs_by_segment: Record<string, number>
  projected_evs: Band
  projected_evs_by_segment: Record<string, Band>
  existing: {
    stations: number
    reported_charge_points: number
    stations_without_counts: number
    charge_points_incl_assumed: number
  }
  public_kwh_per_day: Band
  sessions_per_day: number
  gap_kwh_per_day: number
  additional_charge_points: number
  evs_per_public_charge_point: number | null
  confidence: Record<string, Confidence>
  run: DemandRun
  evidence_ids: string[]
}

export interface ProjectionPoint extends Band {
  year: number
}

export interface Projection {
  scenario: string
  segment: string | null
  observed: ProjectionPoint[]
  forecast: ProjectionPoint[]
  observed_through: string
  confidence: Confidence
}

export interface H3Layer {
  year: number
  scenario: string
  metric: string
  unit: string
  h3: string[]
  value: number[]
  confidence: Confidence
}

export const SEGMENT_LABELS: Record<string, string> = {
  e2w: 'e-2W',
  e3w_passenger: 'e-3W passenger',
  e3w_goods: 'e-3W goods',
  e4w_private: 'e-4W private',
  e4w_fleet_taxi: 'e-4W fleet/taxi',
  ebus: 'e-bus',
  elcv_etruck: 'e-LCV / truck',
}

// --- planning runs (Phase 4) -------------------------------------------------------

export const CHARGER_CLASSES = [
  'AC_7',
  'AC_22',
  'DC_30',
  'DC_60',
  'DC_120',
  'DC_180',
  'DC_240',
  'DC_350',
] as const
export type ChargerClass = (typeof CHARGER_CLASSES)[number]

export interface UserSite {
  name?: string | null
  lat: number
  lng: number
}

export interface ScenarioInput {
  name: string
  target_year: number
  adoption_case: 'slow' | 'base' | 'fast'
  adoption_multiplier?: number
  charger_classes: ChargerClass[]
  budget_inr?: number | null
  max_sites?: number | null
  weight_profile?: string | null
  user_sites: UserSite[]
}

export interface Scenario extends Omit<ScenarioInput, 'user_sites'> {
  id: string
  region_id: string
  adoption_multiplier: number
  budget_inr: number | null
  max_sites: number | null
  weight_profile: string
  user_sites: UserSite[]
  created_at: string
}

export type RunStatus = 'queued' | 'running' | 'succeeded' | 'failed'

export interface ProgressEntry {
  at: string
  step: string
  status: 'started' | 'completed' | 'failed'
  [extra: string]: unknown
}

export interface Funnel {
  theoretical?: { total: number; by_origin: Record<string, number> } & Record<string, unknown>
  candidates?: {
    total: number
    by_origin: Record<string, number>
    merged_within_m: number
    removed_as_duplicates: number
  }
  feasible?: { total: number; by_origin: Record<string, number> }
  rejected?: { total: number; by_reason: Record<string, number> }
  scored?: {
    total: number
    unscored: number
    unscored_reasons: Record<string, number>
    weight_profile: string
    top_n: number
  }
  shortlisted?: number | null
  selected?: number | null
  optimisation?: {
    base_id?: string
    cost_inr?: number
    budget_inr?: number
    coverage_share?: number | null
    n_charge_points?: number
    skipped?: string
  }
}

export const SUB_SCORES = [
  'demand',
  'accessibility',
  'traffic',
  'grid',
  'land',
  'future_growth',
  'competition_gap',
  'equity',
] as const
export type SubScore = (typeof SUB_SCORES)[number]
export type SubScores = Record<SubScore, number>

export interface WeightProfile {
  name: string
  label: string
  version: number | null
  weights: SubScores
}

export interface ScoringSummary {
  method: string
  weight_profile: string
  weights: SubScores
  top_n: number
  shortlist: number
  catchment_minutes: number
  growth_window: [number, number]
  growth_note: string | null
  confidence: Record<SubScore, Confidence>
  total_confidence: Confidence
  uninformative: Partial<Record<SubScore, string>>
  robustness: { draws: number; concentration: number; seed: number }
}

export interface RankedSite {
  site_id: string
  name: string | null
  host_type: string | null
  origin: string
  rank: number
  score_total: number
  robustness: number
  sub_scores: SubScores
}

export interface Ranking {
  weight_profile: string
  weights: SubScores
  top_n: number
  scored: number
  confidence: Record<SubScore, Confidence>
  uninformative: Partial<Record<SubScore, string>>
  method: string
  note: string
  sites: RankedSite[]
}

export type IndexKey =
  | 'population_density'
  | 'evs_per_1000_people'
  | 'demand_density'
  | 'charge_points_per_1000_evs'
  | 'income'
  | 'home_charging_access'

export interface SiteScoring {
  rank: number | null
  score_total: number | null
  sub_scores: SubScores
  robustness: number | null
  unscored?: string
  raw?: Record<string, unknown>
  catchment?: {
    minutes: number
    cells: number
    population: number
    evs: number
    public_kwh_per_day: number
    existing_stations: number
    existing_charge_points: number
  }
  indices?: Record<IndexKey, number | null>
  poi_counts?: Record<string, number>
  run: ScoringSummary | null
}

export interface PlanningRun {
  id: string
  scenario: Scenario
  status: RunStatus
  created_at: string
  started_at: string | null
  finished_at: string | null
  progress: ProgressEntry[]
  funnel: Funnel
  error: string | null
  placeholders_in_use: string[]
  demand_synthetic: boolean | null
  scoring: ScoringSummary | null
}

export type SiteStatus = 'proposed' | 'feasible' | 'rejected'

export interface SiteProperties {
  id: string
  origin: string
  merged_origins: string[]
  host_type: string | null
  name: string | null
  status: SiteStatus
  reason_codes: string[]
  rank: number | null
  score_total: number | null
  selected: boolean
  label: string
}

export type RuleOutcome = 'pass' | 'fail' | 'not_applied' | 'disabled' | 'deferred'

export interface RuleDetail {
  outcome: RuleOutcome
  measured?: unknown
  threshold?: number
  unit?: string
  note?: string
  [extra: string]: unknown
}

export interface SiteDetail {
  id: string
  label: string
  lat: number
  lng: number
  origin: string
  merged_origins: string[]
  host_type: string | null
  host_poi_id: string | null
  name: string | null
  status: SiteStatus
  feasibility: { passed: boolean; reason_codes: string[]; rules: Record<string, RuleDetail> } | null
  features: Record<string, unknown> | null
  scoring: SiteScoring | null
  plan: SitePlan | null
  evidence_ids: string[]
}

export interface WhyNot {
  status: string
  objective_delta: number
  served_delta_kwh: number
  cost_delta_inr: number
  displaced: { site_id: string; name: string | null; rank: number }[]
  spacing_conflicts: { site_id: string; name: string | null }[]
  deciding_sub_score: string
  deciding_points: number
  solve_s: number
}

export interface SitePlan {
  /** The run's base plan, for the Sizing and Finance tabs. */
  optimisation_id: string | null
  selected: boolean
  phase_year: number | null
  coverage_kwh: number | null
  served_population: number | null
  why_not: WhyNot | null
}

export interface PlanKpis {
  coverage_kwh: number
  demand_kwh: number
  coverage_share: number | null
  cost_inr: number
  n_sites: number
  n_charge_points: number
  charger_mix: Record<string, number>
  utilisation: number | null
  equity: number | null
  population_in_catchments: number
  economics?: EconomicsSummary
  top_sites: { site_id: string; name: string | null; host_type: string; served_kwh: number }[]
}

export interface SolverStats {
  solver: string
  status: string
  objective: number | null
  bound: number | null
  mip_gap: number | null
  solve_s: number
  time_limit_s: number
  load_s?: number
  total_s?: number
}

export interface PlanDiffEntry {
  site_id: string
  name: string | null
  bundle?: string
  before?: string
  after?: string
}

export interface PlanDiff {
  base_id: string
  added: PlanDiffEntry[]
  removed: PlanDiffEntry[]
  resized: PlanDiffEntry[]
  kept: number
  delta: Record<'coverage_kwh' | 'cost_inr' | 'n_sites' | 'n_charge_points' | 'equity', number>
}

export type OptimisationKind = 'base' | 'whatif' | 'strategy' | 'pareto'

export interface OptimisationSummary {
  id: string
  run_id: string
  kind: OptimisationKind
  strategy: string
  label: string
  params: {
    budget_inr: number
    max_sites: number
    adoption_multiplier?: number
    budget_share?: number
    demand_scenario: string
    placeholders: string[]
    label?: string
  }
  status: string
  solver_stats: SolverStats
  kpis: PlanKpis
  on_front: boolean
  diff: PlanDiff | null
  created_at: string
}

export interface PlanSite {
  site_id: string
  name: string | null
  host_type: string
  origin: string
  lat: number
  lng: number
  bundle: string
  charge_points: number
  cost_inr: number
  capacity_kwh: number
  served_kwh: number
  served_population: number | null
  utilisation: number | null
  connection: 'LT' | 'HT'
  sanctioned_kw: number
  cost_breakdown: Record<string, number>
  rank: number
}

export interface OptimisationDetail extends OptimisationSummary {
  label_note: string
  sites: PlanSite[]
}

export interface CellAssignment {
  mode: 'assignment' | 'voronoi'
  bands_minutes?: number[]
  cells: Record<string, { site_id: string; served_kwh?: number; minutes?: number }>
}

export interface RejectedSite {
  site_id: string
  name: string | null
  origin: string
  host_type: string | null
  reason_codes: string[]
  reasons: Record<string, RuleDetail>
}

// --- sizing and finance (Phase 7) ---------------------------------------------------

export interface Quantiles {
  p10: number | null
  p50: number | null
  p90: number | null
}

export interface SimResult {
  days: number
  sessions: number
  prob_wait_over: number
  prob_wait_over_peak_hour: number
  mean_wait_min: number
  p95_wait_min: number
  utilisation: number
  hourly_utilisation: number[]
  hourly_prob_wait_over: number[]
}

export interface Sizing {
  method: string
  charger_class: string
  chargers: number
  total_kw: number
  served_kwh_per_day: number
  sessions_per_day: number
  mean_kwh_per_session: number
  mean_service_minutes: number
  segments: {
    segment: string
    kwh_share: number
    session_share: number
    kwh_per_session: number
    service_minutes: number
    effective_kw: number
  }[]
  hourly_arrivals: number[]
  peak_hour: number
  erlang: {
    peak_arrivals_per_hour: number
    prob_wait_over: number
    mean_wait_min: number
    peak_utilisation: number
    capped: boolean
  }
  simulation: SimResult
  targets: { max_wait_minutes: number; max_prob_wait: number; utilisation_band: [number, number] }
  flags: { oversized: boolean; busy: boolean; misses_wait_target_in_simulation: boolean }
  connectors: {
    session_share: Record<string, number>
    guns: Record<string, number>
    guns_per_charger: number
  }
  electrical: {
    sanctioned_kw: number
    diversity_factor: number
    connection: 'LT' | 'HT'
    transformer_required: boolean
    lt_max_sanctioned_kw: number
    connection_cost_inr: number
  }
  plan_bundle: { bundle: string; charge_points: number }
  compute_s: number
}

export interface CashFlowSeries {
  years: number[]
  energy_kwh: number[]
  revenue: number[]
  opex: number[]
  ebitda: number[]
  net: number[]
  capex: number
  npv: number
  irr: number | null
  payback_years: number | null
}

export interface Finance {
  method: string
  subsidy: boolean
  horizon_years: number
  discount_rate: number
  selling_price_inr_per_kwh: number
  grid_energy_inr_per_kwh: number
  connection: 'LT' | 'HT'
  capex_lines: Record<string, number>
  capex_total: number
  capacity_kwh_per_day: number
  base: CashFlowSeries
  cases: Record<'pessimistic' | 'base' | 'optimistic', CashFlowSeries>
  monte_carlo: {
    draws: number
    inputs: Record<string, { low: number; mode: number; high: number; label: string }>
    npv: Quantiles
    irr: Quantiles
    payback_years: Quantiles
    ebitda_year5: Quantiles
    monthly_revenue_steady: Quantiles
    prob_npv_positive: { p: number }
  }
  tornado: {
    input: string
    label: string
    npv_low_input: number
    npv_high_input: number
    swing: number
  }[]
}

export interface SiteEconomics {
  optimisation_id: string
  site_id: string
  label: string
  sizing: Sizing
  finance: Finance
  placeholders: string[]
}

export interface EconomicsSummary {
  sites: number
  skipped: Record<string, string>
  chargers_sized: number
  capex_inr: number
  npv_base_case_inr: number
  npv_p50_sum_inr: number
  sites_npv_positive_p50: number
  subsidy: boolean
}

// --- energy: solar + battery (Phase 8) --------------------------------------------

export interface EnergyScenario {
  label: string
  status: string
  connection: 'LT' | 'HT'
  pv_kwp: number
  battery_kwh: number
  battery_kw: number
  contract_kw: number
  annual: {
    energy: number
    demand: number
    pv: number
    battery: number
    connection: number
    total: number
    opex: number
  }
  capex: { pv: number; battery: number; connection: number }
  grid_kwh: number
  pv_generated_kwh: number
  pv_used_kwh: number
  load_kwh: number
  solar_share: number
  curtailed_share: number
  /** month ("4", "7") -> series -> 24 hourly kW */
  dispatch: Record<string, Record<'pv' | 'grid' | 'ch' | 'dis' | 'soc' | 'pv_available', number[]>>
}

export interface EnergyEffect {
  annual_opex_change: number
  annual_total_change: number
  contract_kw_change: number
  connection_before: 'LT' | 'HT'
  connection_after: 'LT' | 'HT'
  added_capex: number
  simple_payback_years: number | null
}

export interface SiteEnergy {
  optimisation_id: string
  site_id: string
  method: string
  open: {
    scenarios: Record<string, EnergyScenario>
    recommended: string
    effect: EnergyEffect
    pv_limit: { source: string; kwp: number }
    solar_source: string
  }
  google_available: boolean
  google_display: string | null
  google_note: string
  solar: {
    snapshot_id: string
    annual_ghi_kwh_m2: number
    specific_yield_kwh_per_kwp: number
    attribution: string
  }
  load: { hourly_kw: number[]; design_peak_kw: number; annual_kwh: number }
  tariffs: Record<'LT' | 'HT', { price: number[]; demand_charge_kva_month: number }>
  placeholders: string[]
}

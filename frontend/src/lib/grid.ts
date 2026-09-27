/** Grid Impact (module E) payload and helpers. */
import type { Confidence } from './api'

export interface GridAsset {
  asset_id: string
  asset_type: 'transformer' | 'substation'
  name: string | null
  rated_kva: number
  capacity_kva: number
  base_peak_kva: number
  headroom_kva: number
  added_peak_kva: number
  peak_with_kva: number
  utilisation_base: number
  utilisation_with: number
  overloaded: boolean
  upgrade: string | null
  upgrade_cost_inr: number | null
  confidence: Confidence
  site_ids: string[]
  measured_on: string | null
}

export interface GridImpact {
  run_id: string
  optimisation_id: string
  mode: 'proxy' | 'discom' | 'mixed'
  badge: string
  confidence: Confidence
  sites: {
    site_id: string
    name: string | null
    host_type: string
    charger_class: string
    chargers: number
    connection: string
    sanctioned_kw: number
    peak_kw: number
    daily_kwh: number
    grid: { mode: string; asset_id?: string; confidence?: Confidence }
    nearest_osm_substation: { osm_id: string | null; distance_m: number | null }
  }[]
  charger_profile: {
    by_class: Record<string, { sites: number; chargers: number; kw: number }>
    by_host_type: Record<string, number>
  }
  load_curve: {
    by_class: Record<string, number[]>
    by_segment: Record<string, number[]>
    total: number[]
    peak_kw: number
    peak_hour: number
    daily_kwh: number
    sanctioned_kw_total: number
  }
  assets: GridAsset[]
  utilisation: { base: number[]; with_charging: number[] } | null
  upgrades: {
    assets_overloaded: number
    transformers_to_replace: number
    transformer_cost_inr: number
    substations_needing_study: number
  } | null
  proxy_substations: {
    osm_id: string
    voltage_kv: number | null
    site_count: number
    added_peak_kw: number
    sanctioned_kw: number
  }[]
  connections: {
    ht_sites: number
    lt_sites: number
    transformers_required: number
    connection_cost_inr: number
  }
  placeholders: string[]
}

export const SEGMENT_LABELS: Record<string, string> = {
  e2w: 'e-2W',
  e3w: 'e-3W',
  e4w_private: 'e-4W private',
  e4w_fleet_taxi: 'e-4W fleet/taxi',
  ebus: 'e-bus',
  elcv_etruck: 'e-LCV/truck',
}

/** Utilisation bins (share of nameplate): 0-20%, ..., 80-100%, over 100%. */
export const UTILISATION_BINS = ['0–20%', '20–40%', '40–60%', '60–80%', '80–100%', '>100%']

export function binUtilisation(values: number[]): number[] {
  const counts = new Array(UTILISATION_BINS.length).fill(0) as number[]
  for (const v of values) counts[v > 1 ? 5 : Math.min(4, Math.floor(v / 0.2))]++
  return counts
}

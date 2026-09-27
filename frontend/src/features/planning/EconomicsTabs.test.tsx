import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { SiteEconomics } from '../../lib/api'
import { EconomicsTabs } from './EconomicsTabs'

vi.mock('echarts-for-react', () => ({ default: () => null }))

const flows = (npv: number, irr: number | null, payback: number | null) => ({
  years: [0, 1, 2],
  energy_kwh: [0, 1, 2],
  revenue: [0, 1, 2],
  opex: [0, 1, 1],
  ebitda: [0, 0, 1],
  net: [-100, 0, 1],
  capex: 14_000_000,
  npv,
  irr,
  payback_years: payback,
})

const DATA: SiteEconomics = {
  optimisation_id: 'o1',
  site_id: 's1',
  label: 'Candidate site — requires field verification',
  placeholders: ['finance.discount_rate', 'sizing.taper.DC_120'],
  sizing: {
    method: 'sizing_erlang_c_simpy_v1',
    charger_class: 'DC_120',
    chargers: 5,
    total_kw: 600,
    served_kwh_per_day: 2736,
    sessions_per_day: 118,
    mean_kwh_per_session: 23.2,
    mean_service_minutes: 33.4,
    segments: [
      {
        segment: 'e4w_private',
        kwh_share: 0.6,
        session_share: 0.62,
        kwh_per_session: 20,
        service_minutes: 29,
        effective_kw: 50,
      },
    ],
    hourly_arrivals: Array(24).fill(4.9),
    peak_hour: 18,
    erlang: {
      peak_arrivals_per_hour: 9.1,
      prob_wait_over: 0.071,
      mean_wait_min: 2.1,
      peak_utilisation: 0.72,
      capped: false,
    },
    simulation: {
      days: 1000,
      sessions: 118000,
      prob_wait_over: 0.02,
      prob_wait_over_peak_hour: 0.064,
      mean_wait_min: 1.2,
      p95_wait_min: 8.4,
      utilisation: 0.27,
      hourly_utilisation: Array(24).fill(0.27),
      hourly_prob_wait_over: Array(24).fill(0.02),
    },
    targets: { max_wait_minutes: 10, max_prob_wait: 0.1, utilisation_band: [0.1, 0.45] },
    flags: { oversized: false, busy: false, misses_wait_target_in_simulation: false },
    connectors: {
      session_share: { CCS2: 0.93, BHARAT_DC001: 0.07 },
      guns: { CCS2: 9, BHARAT_DC001: 1 },
      guns_per_charger: 2,
    },
    electrical: {
      sanctioned_kw: 480,
      diversity_factor: 0.8,
      connection: 'HT',
      transformer_required: true,
      lt_max_sanctioned_kw: 150,
      connection_cost_inr: 2_500_000,
    },
    plan_bundle: { bundle: 'DC_120x4', charge_points: 4 },
    compute_s: 3.2,
  },
  finance: {
    method: 'finance_mc_v1',
    subsidy: false,
    horizon_years: 10,
    discount_rate: 0.12,
    selling_price_inr_per_kwh: 20,
    grid_energy_inr_per_kwh: 7.5,
    connection: 'HT',
    capex_lines: { chargers: 11_000_000, transformer: 1_500_000 },
    capex_total: 12_500_000,
    capacity_kwh_per_day: 6000,
    base: flows(21_000_000, 0.31, 3.4),
    cases: {
      pessimistic: flows(-2_000_000, 0.08, null),
      base: flows(21_000_000, 0.31, 3.4),
      optimistic: flows(40_000_000, 0.5, 2.1),
    },
    monte_carlo: {
      draws: 5000,
      inputs: { demand: { low: 0.6, mode: 1, high: 1.3, label: 'Demand' } },
      npv: { p10: 5_000_000, p50: 19_000_000, p90: 32_000_000 },
      irr: { p10: 0.15, p50: 0.29, p90: 0.42 },
      payback_years: { p10: 2.5, p50: 3.6, p90: null },
      ebitda_year5: { p10: 1, p50: 2, p90: 3 },
      monthly_revenue_steady: { p10: 1_000_000, p50: 1_600_000, p90: 1_900_000 },
      prob_npv_positive: { p: 0.93 },
    },
    tornado: [
      {
        input: 'demand',
        label: 'Demand',
        npv_low_input: 1_000_000,
        npv_high_input: 30_000_000,
        swing: 29_000_000,
      },
    ],
  },
}

afterEach(() => vi.restoreAllMocks())

function renderTab(tab: 'sizing' | 'finance') {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response(JSON.stringify(DATA), { status: 200 }),
  )
  render(
    <QueryClientProvider client={new QueryClient()}>
      <EconomicsTabs optimisationId="o1" siteId="s1" tab={tab} />
    </QueryClientProvider>,
  )
}

describe('EconomicsTabs', () => {
  it('sizing shows the recommended chargers, queue metrics and the grid connection', async () => {
    renderTab('sizing')
    expect(await screen.findByText('5 × DC 120 kW recommended')).toBeInTheDocument()
    expect(screen.getByText('6.4% simulated')).toBeInTheDocument()
    expect(screen.getByText(/Erlang-C 7.1%/)).toBeInTheDocument()
    expect(screen.getByText('9× CCS2 · 1× BHARAT DC001', { exact: false })).toBeInTheDocument()
    expect(screen.getByText('HT')).toBeInTheDocument()
    expect(
      screen.getByText(/2 costs, tariffs and sizing parameters are placeholders/),
    ).toBeInTheDocument()
  })

  it('finance shows P10/P50/P90, never-pays-back, cases and the subsidy switch', async () => {
    renderTab('finance')
    expect(await screen.findByText('₹0.5 Cr · ₹1.9 Cr · ₹3.2 Cr')).toBeInTheDocument()
    expect(screen.getByText('2.5 · 3.6 · >10')).toBeInTheDocument()
    expect(screen.getByText('93%')).toBeInTheDocument()
    expect(screen.getByText('pessimistic')).toBeInTheDocument()
    expect(screen.getByText('> 10 y')).toBeInTheDocument()
    expect(screen.getByLabelText(/subsidy/i)).not.toBeChecked()
    expect(screen.getByRole('figure', { name: 'Tornado' })).toBeInTheDocument()
  })
})

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { RouterProvider, createMemoryHistory, createRouter } from '@tanstack/react-router'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { router as appRouter } from '../../app/router'
import { binUtilisation, type GridImpact } from '../../lib/grid'

vi.mock('echarts-for-react', () => ({ default: () => null }))

const flat = (v: number) => Array.from({ length: 24 }, () => v)

const proxy: GridImpact = {
  run_id: 'r1',
  optimisation_id: 'o1',
  mode: 'proxy',
  badge: 'Proximity-only estimate: no DISCOM loading data for these sites',
  confidence: 'LOW',
  sites: [],
  charger_profile: {
    by_class: { DC_120: { sites: 2, chargers: 12, kw: 1440 } },
    by_host_type: { FUEL_STATION: 2 },
  },
  load_curve: {
    by_class: { DC_120: flat(100) },
    by_segment: { ebus: flat(40), e4w_private: flat(60) },
    total: flat(100),
    peak_kw: 100,
    peak_hour: 18,
    daily_kwh: 2400,
    sanctioned_kw_total: 1152,
  },
  assets: [],
  utilisation: null,
  upgrades: null,
  proxy_substations: [
    { osm_id: 'W123', voltage_kv: 22, site_count: 2, added_peak_kw: 100, sanctioned_kw: 1152 },
  ],
  connections: { ht_sites: 2, lt_sites: 0, transformers_required: 2, connection_cost_inr: 5000000 },
  placeholders: ['grid.coincidence_factor'],
}

const discom: GridImpact = {
  ...proxy,
  mode: 'discom',
  badge: 'DISCOM asset ratings and loadings',
  confidence: 'MEDIUM',
  proxy_substations: [],
  assets: [
    {
      asset_id: 'MSEDCL:SS-1',
      asset_type: 'substation',
      name: 'Hinjewadi SS',
      rated_kva: 20000,
      capacity_kva: 16000,
      base_peak_kva: 15990,
      headroom_kva: 10,
      added_peak_kva: 111,
      peak_with_kva: 16101,
      utilisation_base: 0.8,
      utilisation_with: 0.805,
      overloaded: true,
      upgrade: 'substation augmentation: needs a DISCOM system study (not costed)',
      upgrade_cost_inr: null,
      confidence: 'MEDIUM',
      site_ids: ['s1', 's2'],
      measured_on: '2026-08-28',
    },
  ],
  utilisation: { base: [0.8], with_charging: [0.805] },
  upgrades: {
    assets_overloaded: 1,
    transformers_to_replace: 0,
    transformer_cost_inr: 0,
    substations_needing_study: 1,
  },
}

function renderAt(payload: GridImpact) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({ ok: true, json: async () => payload })),
  )
  const router = createRouter({
    routeTree: appRouter.routeTree,
    history: createMemoryHistory({ initialEntries: ['/runs/r1/grid'] }),
  })
  render(
    <QueryClientProvider client={new QueryClient()}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
}

afterEach(() => vi.unstubAllGlobals())

describe('GridImpactPage', () => {
  it('labels a proximity-only estimate and shows no utilisation', async () => {
    renderAt(proxy)
    expect(await screen.findByText(/Proximity-only estimate/)).toBeInTheDocument()
    expect(screen.getByText('low confidence')).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Nearest substations' })).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Asset utilisation' })).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Table' }))
    expect(screen.getByRole('columnheader', { name: 'e-bus' })).toBeInTheDocument()
  })

  it('switches to DISCOM-backed utilisation and upgrades', async () => {
    renderAt(discom)
    expect(await screen.findByText(/DISCOM asset ratings and loadings/)).toBeInTheDocument()
    expect(screen.getAllByText('medium confidence').length).toBeGreaterThan(0)
    expect(screen.getByText('Hinjewadi SS')).toBeInTheDocument()
    expect(screen.getByText(/over limit/)).toBeInTheDocument()
    expect(screen.getByText('Substations needing a DISCOM study')).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Nearest substations' })).toBeNull()
  })

  it('bins utilisation including overloads', () => {
    expect(binUtilisation([0.1, 0.39, 0.8, 1.0, 1.2])).toEqual([1, 1, 0, 0, 2, 1])
  })
})

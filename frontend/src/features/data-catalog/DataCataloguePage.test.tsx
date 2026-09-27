import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { DataSource } from '../../lib/api'
import { DataCataloguePage } from './DataCataloguePage'

const SOURCES: DataSource[] = [
  {
    id: 'osm',
    name: 'OpenStreetMap',
    license_class: 'OPEN',
    license_text: 'ODbL 1.0',
    attribution: '© OpenStreetMap contributors',
    terms_url: null,
    refresh_cadence: 'daily',
    freshness: 'fresh',
    datasets: [
      {
        id: 's1',
        retrieved_at: '2026-09-24T00:00:00Z',
        period_start: null,
        period_end: null,
        granularity: 'point',
        row_count: 47456,
        stored: true,
        checksum: 'sha256:x',
        quality_report: { row_count: 47456 },
      },
    ],
  },
  {
    id: 'synthetic_vahan',
    name: 'Synthetic registrations (VAHAN schema) — NOT REAL DATA',
    license_class: 'SYNTHETIC',
    license_text: null,
    attribution: null,
    terms_url: null,
    refresh_cadence: 'on demand',
    freshness: 'never',
    datasets: [],
  },
]

const LIVE: DataSource = {
  id: 'google_places',
  name: 'Google Places API (New)',
  license_class: 'RESTRICTED_GOOGLE',
  license_text: null,
  attribution: '© Google',
  terms_url: null,
  refresh_cadence: 'on demand',
  freshness: 'live',
  access: 'live',
  usage: { calls_this_month: 3, cost_inr_this_month: 0, last_used: '2026-09-27T12:32:25Z' },
  datasets: [],
}

const READINESS = [
  {
    region_id: 'pmr',
    name: 'Pune Metropolitan Region',
    status: 'onboarded',
    score: 71.5,
    grade: 'B',
    can_plan: true,
    blockers: [],
    dimensions: {
      registrations: {
        score: 0.25,
        weight: 0.16,
        detail: 'synthetic only (1200 rows)',
        fix: 'import a VAHAN export',
      },
      roads_pois: { score: 1, weight: 0.14, detail: '120000 road segments', fix: '' },
    },
  },
]

afterEach(() => vi.restoreAllMocks())

describe('DataCataloguePage', () => {
  it('shows each source with its licence, freshness and row count', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) =>
      String(input).includes('/readiness')
        ? new Response(JSON.stringify(READINESS), { status: 200 })
        : new Response(JSON.stringify([...SOURCES, LIVE]), { status: 200 }),
    )
    render(
      <QueryClientProvider client={new QueryClient()}>
        <DataCataloguePage />
      </QueryClientProvider>,
    )
    expect(await screen.findByText('OpenStreetMap')).toBeInTheDocument()
    expect(screen.getByText('47,456')).toBeInTheDocument()
    expect(screen.getByText('24 Sep 2026')).toBeInTheDocument()
    expect(screen.getByText('Synthetic — not real data')).toBeInTheDocument()
    expect(screen.getByText('not ingested')).toBeInTheDocument()
    expect(screen.getByText('live · not stored')).toBeInTheDocument()
    expect(screen.getByText('3 calls this month')).toBeInTheDocument()
    expect(await screen.findByText('Pune Metropolitan Region')).toBeInTheDocument()
    expect(screen.getByText('72 · B')).toBeInTheDocument()
    expect(
      screen.getByText(/synthetic only \(1200 rows\)/, { selector: 'div' }),
    ).toBeInTheDocument()
  })
})

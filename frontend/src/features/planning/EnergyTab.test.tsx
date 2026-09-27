import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { SiteEnergy } from '../../lib/api'
import { EnergyTab } from './EnergyTab'
// A real response for a Pune plan site (synthetic demand, illustrative prices).
import fixture from './__fixtures__/energy.json'

vi.mock('echarts-for-react', () => ({ default: () => null }))

afterEach(() => vi.restoreAllMocks())

function renderTab(data: SiteEnergy) {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response(JSON.stringify(data), { status: 200 }),
  )
  render(
    <QueryClientProvider client={new QueryClient()}>
      <EnergyTab optimisationId="o1" siteId="s1" />
    </QueryClientProvider>,
  )
}

describe('EnergyTab', () => {
  it('states the recommendation and its effect on the connection and OPEX', async () => {
    const data = fixture as unknown as SiteEnergy
    renderTab(data)
    const rec = data.open.scenarios[data.open.recommended]
    expect(await screen.findByText(`Recommended: ${rec.label}`)).toBeInTheDocument()
    expect(screen.getByText('HT → LT')).toBeInTheDocument()
    expect(screen.getByText(/^−₹28\.6 L\/yr$/)).toBeInTheDocument()
    expect(screen.getByText(/simple payback 4 years/)).toBeInTheDocument()
    // Every option is listed, the recommended one marked.
    expect(screen.getByText('Grid only')).toBeInTheDocument()
    expect(screen.getByText(`${rec.label} ✓`)).toBeInTheDocument()
    expect(screen.getByText(/Google Solar not configured/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('radio', { name: 'Jul' }))
    expect(screen.getByRole('radio', { name: 'Jul' })).toHaveAttribute('aria-checked', 'true')
  })

  it('marks an infeasible option instead of hiding it', async () => {
    const data = structuredClone(fixture) as unknown as SiteEnergy
    data.open.scenarios.pv_battery_lt = {
      ...data.open.scenarios.pv_battery_lt,
      status: 'infeasible',
    }
    data.open.recommended = 'pv_battery'
    renderTab(data)
    expect(await screen.findByText('not feasible')).toBeInTheDocument()
  })
})

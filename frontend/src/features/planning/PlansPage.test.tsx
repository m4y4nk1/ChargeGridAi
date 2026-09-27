import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ScenarioForm } from './PlansPage'

afterEach(() => vi.restoreAllMocks())

const PROFILES = [
  { name: 'highway', label: 'Highway operator', version: 1, weights: {} },
  { name: 'cpo_commercial', label: 'CPO commercial', version: 1, weights: {} },
]

/** Answers /weight-profiles itself and hands every other request to `rest` in order. */
function mockApi(...rest: Response[]) {
  return vi
    .spyOn(globalThis, 'fetch')
    .mockImplementation(async (input) =>
      String(input).endsWith('/weight-profiles')
        ? new Response(JSON.stringify(PROFILES), { status: 200 })
        : (rest.shift() ?? new Response('{}', { status: 500 })),
    )
}

const posts = (m: ReturnType<typeof mockApi>) =>
  m.mock.calls.filter(([, init]) => init?.method === 'POST')

function renderForm(onCreated = vi.fn()) {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ScenarioForm onCreated={onCreated} />
    </QueryClientProvider>,
  )
  return onCreated
}

describe('ScenarioForm', () => {
  it('creates a scenario, then starts a run for it', async () => {
    const fetchMock = mockApi(
      new Response(JSON.stringify({ id: 'sc1' }), { status: 201 }),
      new Response(JSON.stringify({ id: 'run1' }), { status: 202 }),
    )
    const onCreated = renderForm()

    await screen.findByRole('option', { name: 'Highway operator' })
    fireEvent.change(screen.getByLabelText('Weight profile'), { target: { value: 'highway' } })
    fireEvent.change(screen.getByLabelText(/your candidate sites/i), {
      target: { value: '18.53, 73.85, Depot' },
    })
    fireEvent.click(screen.getByLabelText('DC 120 kW'))
    fireEvent.change(screen.getByLabelText(/budget/i), { target: { value: '10' } })
    fireEvent.change(screen.getByLabelText('Max sites'), { target: { value: '20' } })
    fireEvent.click(screen.getByRole('button', { name: 'Generate candidates' }))

    await waitFor(() => expect(onCreated).toHaveBeenCalled())
    const [scenarioCall, runCall] = posts(fetchMock)
    expect(String(scenarioCall[0])).toMatch(/\/api\/v1\/scenarios$/)
    expect(JSON.parse(String(scenarioCall[1]?.body))).toMatchObject({
      target_year: 2030,
      adoption_case: 'base',
      charger_classes: ['DC_60', 'DC_120'],
      weight_profile: 'highway',
      budget_inr: 100_000_000,
      max_sites: 20,
      user_sites: [{ lat: 18.53, lng: 73.85, name: 'Depot' }],
    })
    expect(JSON.parse(String(runCall[1]?.body))).toEqual({ scenario_id: 'sc1' })
    expect(onCreated.mock.calls[0][0]).toMatchObject({ id: 'run1' })
  })

  it('blocks submission while a site line is malformed', () => {
    const fetchMock = mockApi()
    renderForm()
    fireEvent.change(screen.getByLabelText(/your candidate sites/i), {
      target: { value: 'not a site' },
    })
    expect(screen.getByText('Line 1: expected "lat, lng, name"')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Generate candidates' })).toBeDisabled()
    expect(posts(fetchMock)).toHaveLength(0)
  })

  it('shows API validation messages', async () => {
    mockApi(
      new Response(
        JSON.stringify({
          detail: [{ loc: ['body'], msg: 'site latitude is outside the modelled Pune region' }],
        }),
        { status: 422 },
      ),
    )
    renderForm()
    fireEvent.click(screen.getByRole('button', { name: 'Generate candidates' }))
    expect(await screen.findByText(/outside the modelled Pune region/)).toBeInTheDocument()
  })
})

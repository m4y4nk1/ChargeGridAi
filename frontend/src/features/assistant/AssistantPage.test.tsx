import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { RouterProvider, createMemoryHistory, createRouter } from '@tanstack/react-router'
import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { router as appRouter } from '../../app/router'
import type { AgentSession } from '../../lib/agents'

const session: AgentSession = {
  id: 's1',
  status: 'completed',
  request: 'Find 20 fast-charging locations along Mumbai–Pune',
  region_id: 'mumbai_pune',
  run_id: 'r1',
  scenario_id: 'sc1',
  summary: {
    plan: {
      intent: 'plan',
      restated_request: 'Find 20 fast-charging sites along the Mumbai–Pune corridor',
      scenario: {
        region_id: 'mumbai_pune',
        target_year: 2030,
        charger_classes: ['DC_60', 'DC_120'],
        budget_inr: 300000000,
        max_sites: 20,
      },
      steps: [{ agent: 'geo', goal: 'Existing chargers on the corridor' }],
      consult: [],
      assumptions: ['5 km buffer'],
      questions_for_user: [],
    },
    report: {
      markdown: '## Answer\n\nThe plan selects 20 candidate sites costing ₹28.78 crore.',
      validation: {
        numbers_checked: 2,
        unmatched_after: 0,
        regenerations: 1,
        removed_sentences: [],
      },
      evidence_ids: ['e1'],
      links: { run_id: 'r1', optimisation_id: 'o1', scenario_id: 'sc1' },
    },
  },
  cost_usd: 0.42,
  input_tokens: 1000,
  output_tokens: 200,
  llm_calls: 12,
  tool_calls: 9,
  error: null,
  created_at: '2026-09-27T00:00:00Z',
  updated_at: '2026-09-27T00:00:00Z',
}

class FakeEventSource {
  addEventListener() {}
  close() {}
}

afterEach(() => vi.unstubAllGlobals())

describe('AssistantPage', () => {
  it('shows the plan and the validated report of a finished session', async () => {
    vi.stubGlobal('EventSource', FakeEventSource)
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => ({
        ok: true,
        json: async () => (url.endsWith('/agent/sessions') ? [session] : session),
      })),
    )
    const router = createRouter({
      routeTree: appRouter.routeTree,
      history: createMemoryHistory({ initialEntries: ['/assistant/s1'] }),
    })
    render(
      <QueryClientProvider client={new QueryClient()}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    )
    expect(
      await screen.findByText('Find 20 fast-charging sites along the Mumbai–Pune corridor'),
    ).toBeInTheDocument()
    expect(screen.getByText(/costing ₹28.78 crore/)).toBeInTheDocument()
    expect(
      screen.getByText(/2 numbers checked against tool results · redrafted 1×/),
    ).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Open the plan on the map' })).toHaveAttribute(
      'href',
      '/runs/r1',
    )
    expect(screen.getByLabelText('Follow-up question')).toBeInTheDocument()
  })
})

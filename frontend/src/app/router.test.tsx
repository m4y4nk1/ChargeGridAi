import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { RouterProvider } from '@tanstack/react-router'
import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { router } from './router'

afterEach(() => vi.unstubAllGlobals())

describe('router', () => {
  it('renders the overview page at the index route', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: true,
        status: 200,
        json: async () => ({
          label: 'anonymous (dev)',
          method: 'anonymous',
          org: 'default',
          roles: ['planner'],
        }),
      })),
    )
    render(
      <QueryClientProvider client={new QueryClient()}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    )
    expect(await screen.findByRole('heading', { name: 'ChargeGrid AI' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /open workspace/i })).toBeInTheDocument()
    expect(await screen.findByText('anonymous (dev) · default')).toBeInTheDocument()
  })
})

/** Sign in: local account (email + password) or single sign-on (OIDC + PKCE). */
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { useState } from 'react'
import { API_URL, getJson } from '../../lib/api'
import { type AuthConfig, setToken, startSso } from '../../lib/auth'

export function LoginPage() {
  const navigate = useNavigate()
  const cfg = useQuery({
    queryKey: ['auth-config'],
    queryFn: () => getJson<AuthConfig>('/auth/config'),
  })
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const local = async () => {
    setBusy(true)
    setError(null)
    try {
      const r = await fetch(`${API_URL}/api/v1/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: new URLSearchParams({ username: email, password }),
      })
      if (!r.ok)
        throw new Error(
          r.status === 400 ? 'Wrong email or password' : `Sign-in failed (${r.status})`,
        )
      setToken(((await r.json()) as { access_token: string }).access_token)
      void navigate({ to: '/' })
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mx-auto flex max-w-sm flex-col gap-4 p-8">
      <h1 className="text-lg font-semibold">Sign in to ChargeGrid AI</h1>
      {cfg.data && !cfg.data.required && (
        <p className="text-sm text-muted">
          Sign-in is optional on this server (development mode): without it you act as an anonymous
          planner.
        </p>
      )}
      {cfg.data?.oidc && (
        <button
          type="button"
          className="rounded bg-ink px-3 py-2 text-sm text-paper"
          onClick={() => startSso(cfg.data!.oidc!).catch((e: Error) => setError(e.message))}
        >
          Sign in with single sign-on
        </button>
      )}
      <form
        className="flex flex-col gap-2"
        onSubmit={(e) => {
          e.preventDefault()
          void local()
        }}
      >
        <label className="flex flex-col gap-1 text-sm">
          Email
          <input
            type="email"
            autoComplete="username"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="rounded border border-line bg-panel px-2 py-1"
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          Password
          <input
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="rounded border border-line bg-panel px-2 py-1"
          />
        </label>
        <button
          type="submit"
          disabled={busy || !email || !password}
          className="rounded border border-line px-3 py-1.5 text-sm disabled:opacity-50"
        >
          {busy ? 'Signing in…' : 'Sign in with email'}
        </button>
      </form>
      {error && (
        <p role="alert" className="text-sm text-warn">
          {error}
        </p>
      )}
    </div>
  )
}

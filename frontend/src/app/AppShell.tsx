import { Link, Outlet, useNavigate } from '@tanstack/react-router'
import { useEffect } from 'react'
import { setToken } from '../lib/auth'
import { DEFAULT_AREA_ID } from '../lib/regions'
import { useMe } from './useMe'

export function AppShell() {
  const link = 'text-sm text-muted hover:text-ink [&.active]:font-semibold [&.active]:text-ink'
  const data = useMe().data
  // Only trust a well-formed principal (a failed or odd response leaves the header plain).
  const me = data && Array.isArray(data.roles) ? data : undefined
  const navigate = useNavigate()
  useEffect(() => {
    const signIn = () => void navigate({ to: '/login' })
    window.addEventListener('chargegrid-signin-required', signIn)
    return () => window.removeEventListener('chargegrid-signin-required', signIn)
  }, [navigate])
  const admin = me?.roles.some((r) => r === 'admin' || r === 'org_admin')
  return (
    <div className="grid h-full grid-rows-[auto_1fr]">
      <header className="flex items-center gap-6 border-b border-line bg-panel px-4 py-2">
        <Link to="/" className="font-semibold tracking-tight">
          ChargeGrid AI
        </Link>
        <nav className="flex gap-4">
          <Link to="/workspace" className={link}>
            Workspace
          </Link>
          <Link to="/areas/$regionId" params={{ regionId: DEFAULT_AREA_ID }} className={link}>
            Area planner
          </Link>
          <Link to="/plans" className={link}>
            Site planning
          </Link>
          <Link to="/twin" className={link}>
            Scenarios
          </Link>
          <Link to="/assistant" className={link}>
            Assistant
          </Link>
          <Link to="/data" className={link}>
            Data
          </Link>
          {admin && (
            <Link to="/admin" className={link}>
              Admin
            </Link>
          )}
        </nav>
        <span className="ml-auto flex items-center gap-3 text-xs text-muted">
          {me && (
            <span title={`Roles: ${me.roles.join(', ')}`}>
              {me.label} · {me.org}
            </span>
          )}
          {me?.method === 'anonymous' ? (
            <Link to="/login" className="underline">
              Sign in
            </Link>
          ) : (
            me && (
              <button type="button" className="underline" onClick={() => setToken(null)}>
                Sign out
              </button>
            )
          )}
        </span>
      </header>
      <div className="min-h-0">
        <Outlet />
      </div>
    </div>
  )
}

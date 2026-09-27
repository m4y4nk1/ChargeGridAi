/**
 * Sign-in state for the web app (Section 14). In dev the API allows anonymous use;
 * elsewhere every call carries a bearer token from either local sign-in
 * (fastapi-users) or single sign-on (OIDC authorization code + PKCE, e.g. Keycloak).
 * Tokens live in sessionStorage, so closing the tab signs out.
 */
const TOKEN_KEY = 'chargegrid.token'
const PKCE_KEY = 'chargegrid.pkce'

export function getToken(): string | null {
  try {
    return sessionStorage.getItem(TOKEN_KEY)
  } catch {
    return null
  }
}

export function setToken(token: string | null): void {
  try {
    if (token) sessionStorage.setItem(TOKEN_KEY, token)
    else sessionStorage.removeItem(TOKEN_KEY)
  } catch {
    /* storage unavailable: the session lasts until reload */
  }
  window.dispatchEvent(new Event('chargegrid-auth'))
}

export function authHeaders(): Record<string, string> {
  const token = getToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

export interface AuthConfig {
  required: boolean
  local: boolean
  oidc: { issuer: string; client_id: string } | null
}

function base64url(bytes: ArrayBuffer | Uint8Array): string {
  const arr = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes)
  return btoa(String.fromCharCode(...arr))
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/, '')
}

async function discovery(
  issuer: string,
): Promise<{ authorization_endpoint: string; token_endpoint: string }> {
  const r = await fetch(`${issuer.replace(/\/$/, '')}/.well-known/openid-configuration`)
  if (!r.ok) throw new Error('Single sign-on is unreachable')
  return (await r.json()) as { authorization_endpoint: string; token_endpoint: string }
}

/** Start single sign-on: redirect to the identity provider with a PKCE challenge. */
export async function startSso(oidc: NonNullable<AuthConfig['oidc']>): Promise<void> {
  const verifier = base64url(crypto.getRandomValues(new Uint8Array(32)))
  const state = base64url(crypto.getRandomValues(new Uint8Array(16)))
  const challenge = base64url(
    await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier)),
  )
  sessionStorage.setItem(PKCE_KEY, JSON.stringify({ verifier, state, ...oidc }))
  const { authorization_endpoint } = await discovery(oidc.issuer)
  const url = new URL(authorization_endpoint)
  url.search = new URLSearchParams({
    response_type: 'code',
    client_id: oidc.client_id,
    redirect_uri: `${window.location.origin}/auth/callback`,
    scope: 'openid email profile',
    code_challenge: challenge,
    code_challenge_method: 'S256',
    state,
  }).toString()
  window.location.assign(url.toString())
}

/** Finish single sign-on on /auth/callback: check state, exchange the code. */
export async function finishSso(search: URLSearchParams): Promise<void> {
  const saved = JSON.parse(sessionStorage.getItem(PKCE_KEY) ?? 'null') as {
    verifier: string
    state: string
    issuer: string
    client_id: string
  } | null
  sessionStorage.removeItem(PKCE_KEY)
  if (!saved || search.get('state') !== saved.state)
    throw new Error('Sign-in was interrupted; try again')
  const code = search.get('code')
  if (!code) throw new Error(search.get('error_description') ?? 'Sign-in was cancelled')
  const { token_endpoint } = await discovery(saved.issuer)
  const r = await fetch(token_endpoint, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({
      grant_type: 'authorization_code',
      code,
      client_id: saved.client_id,
      redirect_uri: `${window.location.origin}/auth/callback`,
      code_verifier: saved.verifier,
    }),
  })
  if (!r.ok) throw new Error('Sign-in failed')
  setToken(((await r.json()) as { access_token: string }).access_token)
}

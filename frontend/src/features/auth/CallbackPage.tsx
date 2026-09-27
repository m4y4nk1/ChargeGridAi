/** Single sign-on return: exchange the authorization code, then go home. */
import { useNavigate } from '@tanstack/react-router'
import { useEffect, useState } from 'react'
import { finishSso } from '../../lib/auth'

export function CallbackPage() {
  const navigate = useNavigate()
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    finishSso(new URLSearchParams(window.location.search))
      .then(() => navigate({ to: '/' }))
      .catch((e: Error) => setError(e.message))
  }, [navigate])
  return (
    <p className="p-8 text-sm">
      {error ? (
        <span role="alert" className="text-warn">
          {error}
        </span>
      ) : (
        'Signing in…'
      )}
    </p>
  )
}

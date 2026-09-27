import { useQuery } from '@tanstack/react-query'
import { useEffect } from 'react'
import { getJson } from '../lib/api'

export interface Me {
  user_id: string | null
  label: string
  method: 'local' | 'oidc' | 'anonymous'
  org: string
  roles: string[]
  auth_required: boolean
}

/** The signed-in principal; refetched when the token changes. */
export function useMe() {
  const q = useQuery({ queryKey: ['me'], queryFn: () => getJson<Me>('/me'), retry: false })
  useEffect(() => {
    const refresh = () => void q.refetch()
    window.addEventListener('chargegrid-auth', refresh)
    return () => window.removeEventListener('chargegrid-auth', refresh)
  }, [q])
  return q
}

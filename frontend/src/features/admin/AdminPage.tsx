/**
 * Administration: versioned config (platform admins), the audit log, and the members of
 * your organisation (org admins). Every change here is audited by the API.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { formatDate } from '../../lib/format'
import { getJson, postJson, putJson } from '../../lib/api'
import type { Me } from '../../app/useMe'

type Tab = 'config' | 'audit' | 'members'

function ConfigEditor() {
  const qc = useQueryClient()
  const keys = useQuery({
    queryKey: ['admin-config'],
    queryFn: () => getJson<{ key: string; version: number }[]>('/admin/config'),
  })
  const [key, setKey] = useState<string | null>(null)
  const item = useQuery({
    queryKey: ['admin-config', key],
    queryFn: () =>
      getJson<{
        content: string
        version: number
        history: { version: number; author: string; at: string; note: string | null }[]
      }>(`/admin/config/${key}`),
    enabled: !!key,
  })
  const [text, setText] = useState('')
  const [note, setNote] = useState('')
  useEffect(() => setText(item.data?.content ?? ''), [item.data])
  const save = useMutation({
    mutationFn: () => putJson(`/admin/config/${key}`, { content: text, note: note || null }),
    onSuccess: () => {
      setNote('')
      void qc.invalidateQueries({ queryKey: ['admin-config'] })
    },
  })
  const rollback = useMutation({
    mutationFn: (version: number) => postJson(`/admin/config/${key}/rollback`, { version }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['admin-config'] }),
  })
  if (keys.error)
    return (
      <p className="text-sm text-muted">
        Config is managed by platform admins (admin in the default organisation).
      </p>
    )
  return (
    <div className="grid grid-cols-[16rem_1fr] gap-4">
      <ul className="max-h-[70vh] overflow-y-auto text-xs" aria-label="Config files">
        {(keys.data ?? []).map((k) => (
          <li key={k.key}>
            <button
              type="button"
              onClick={() => setKey(k.key)}
              className={`w-full px-2 py-1 text-left ${key === k.key ? 'bg-ink text-paper' : 'hover:bg-panel'}`}
            >
              {k.key}
              {k.version > 0 && <span className="ml-1 opacity-70">v{k.version}</span>}
            </button>
          </li>
        ))}
      </ul>
      {key && item.data && (
        <div className="flex flex-col gap-2">
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            spellCheck={false}
            rows={24}
            aria-label="Config content"
            className="rounded border border-line bg-panel p-2 font-mono text-xs"
          />
          <div className="flex gap-2">
            <input
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="What changed and why (source)"
              className="flex-1 rounded border border-line bg-panel px-2 py-1 text-sm"
            />
            <button
              type="button"
              disabled={save.isPending || text === item.data.content}
              onClick={() => save.mutate()}
              className="rounded bg-ink px-3 py-1 text-sm text-paper disabled:opacity-50"
            >
              Save as v{item.data.version + 1}
            </button>
          </div>
          {(save.error || rollback.error) && (
            <p role="alert" className="text-sm text-warn">
              {(save.error ?? rollback.error)!.message}
            </p>
          )}
          <details className="text-xs">
            <summary>History</summary>
            <ul>
              {item.data.history.map((h) => (
                <li key={h.version}>
                  v{h.version} · {h.author} · {formatDate(h.at)} {h.note && `· ${h.note}`}{' '}
                  <button
                    type="button"
                    className="underline"
                    onClick={() => rollback.mutate(h.version)}
                  >
                    restore
                  </button>
                </li>
              ))}
              <li>
                v0 (file shipped with the code){' '}
                <button type="button" className="underline" onClick={() => rollback.mutate(0)}>
                  restore
                </button>
              </li>
            </ul>
          </details>
        </div>
      )}
    </div>
  )
}

function AuditLog() {
  const q = useQuery({
    queryKey: ['admin-audit'],
    queryFn: () =>
      getJson<
        {
          id: number
          at: string
          actor: string
          action: string
          target_type: string | null
          target_id: string | null
          payload: Record<string, unknown>
        }[]
      >('/admin/audit?limit=200'),
  })
  if (q.error) return <p className="text-sm text-muted">The audit log needs the admin role.</p>
  return (
    <table className="w-full text-xs">
      <thead>
        <tr className="text-left text-muted">
          <th>When</th>
          <th>Who</th>
          <th>Action</th>
          <th>Target</th>
          <th>Details</th>
        </tr>
      </thead>
      <tbody>
        {(q.data ?? []).map((a) => (
          <tr key={a.id} className="border-t border-line align-top">
            <td className="whitespace-nowrap">{formatDate(a.at)}</td>
            <td>{a.actor}</td>
            <td>{a.action}</td>
            <td>
              {a.target_type}:{a.target_id?.slice(0, 36)}
            </td>
            <td className="font-mono">{JSON.stringify(a.payload).slice(0, 120)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function Members({ org }: { org: string }) {
  const qc = useQueryClient()
  const q = useQuery({
    queryKey: ['members', org],
    queryFn: () => getJson<{ email: string; roles: string[] }[]>(`/admin/orgs/${org}/members`),
  })
  const [email, setEmail] = useState('')
  const [roles, setRoles] = useState<string[]>(['viewer'])
  const set = useMutation({
    mutationFn: (body: { email: string; roles: string[] }) =>
      putJson(`/admin/orgs/${org}/members`, body),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['members', org] }),
  })
  if (q.error)
    return <p className="text-sm text-muted">Managing members needs the org_admin role in {org}.</p>
  return (
    <div className="flex flex-col gap-3 text-sm">
      <table className="w-full text-xs">
        <tbody>
          {(q.data ?? []).map((m) => (
            <tr key={m.email} className="border-t border-line">
              <td>{m.email}</td>
              <td>{m.roles.join(', ')}</td>
              <td>
                <button
                  type="button"
                  className="underline"
                  onClick={() => set.mutate({ email: m.email, roles: [] })}
                >
                  remove
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <form
        className="flex flex-wrap items-center gap-2"
        onSubmit={(e) => {
          e.preventDefault()
          set.mutate({ email, roles })
        }}
      >
        <input
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="member@example.org"
          className="rounded border border-line bg-panel px-2 py-1"
        />
        {['viewer', 'planner', 'admin', 'org_admin'].map((r) => (
          <label key={r} className="flex items-center gap-1 text-xs">
            <input
              type="checkbox"
              checked={roles.includes(r)}
              onChange={(e) =>
                setRoles((rs) => (e.target.checked ? [...rs, r] : rs.filter((x) => x !== r)))
              }
            />
            {r}
          </label>
        ))}
        <button
          type="submit"
          disabled={!email}
          className="rounded bg-ink px-3 py-1 text-paper disabled:opacity-50"
        >
          Add / update
        </button>
      </form>
      {set.error && (
        <p role="alert" className="text-warn">
          {set.error.message}
        </p>
      )}
    </div>
  )
}

export function AdminPage({ me }: { me: Me | undefined }) {
  const [tab, setTab] = useState<Tab>('audit')
  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-4 p-6">
      <h1 className="text-lg font-semibold">Administration</h1>
      <div role="tablist" className="flex gap-1 text-sm">
        {(['audit', 'config', 'members'] as const).map((t) => (
          <button
            key={t}
            type="button"
            role="tab"
            aria-selected={tab === t}
            onClick={() => setTab(t)}
            className={`rounded px-3 py-1 ${tab === t ? 'bg-ink text-paper' : 'border border-line'}`}
          >
            {t === 'audit' ? 'Audit log' : t === 'config' ? 'Config' : 'Members'}
          </button>
        ))}
      </div>
      {tab === 'config' && <ConfigEditor />}
      {tab === 'audit' && <AuditLog />}
      {tab === 'members' && <Members org={me?.org ?? 'default'} />}
    </div>
  )
}

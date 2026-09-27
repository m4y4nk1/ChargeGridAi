/**
 * Planning assistant (Section 10): ask a question or request a plan; the agents' plan,
 * steps, tool calls and run progress stream in; costly optimisation waits for the user's
 * confirmation; the report arrives with its numeric-validation result.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate } from '@tanstack/react-router'
import { useEffect, useRef, useState } from 'react'
import { Markdown } from '../../components/Markdown'
import { getJson, postJson, streamUrl } from '../../lib/api'
import { formatCrore } from '../../lib/format'
import {
  type AgentEvent,
  type AgentPlan,
  type AgentReport,
  type AgentSession,
  type Confirmation,
  EVENT_TYPES,
  type SessionStatus,
  agentLabel,
  runProgress,
  streamedText,
  timeline,
} from '../../lib/agents'

const EXAMPLES = [
  'Find 20 fast-charging locations along Mumbai–Pune with a ₹30 crore budget',
  'How many public DC fast-charging stations are there in the Pune region?',
  'What does the PM E-DRIVE scheme subsidise for public charging stations?',
  'Where are the biggest unserved charging demand hotspots in Pune in 2028?',
]

const STATUS_LABEL: Record<SessionStatus, string> = {
  running: 'working',
  awaiting_confirmation: 'needs your confirmation',
  completed: 'done',
  failed: 'failed',
  cancelled: 'cancelled',
  stopped: 'stopped early',
  interrupted: 'interrupted',
}

export function AssistantPage({ sessionId, runId }: { sessionId?: string; runId?: string }) {
  const sessions = useQuery({
    queryKey: ['agent-sessions'],
    queryFn: () => getJson<AgentSession[]>('/agent/sessions'),
    refetchInterval: 5000,
  })
  return (
    <div className="grid h-full grid-cols-[16rem_1fr]">
      <aside className="flex min-h-0 flex-col border-r border-line bg-panel">
        <div className="flex items-center justify-between px-3 py-2">
          <h2 className="text-sm font-semibold">Assistant</h2>
          <Link to="/assistant" className="text-xs underline">
            New
          </Link>
        </div>
        <ul className="min-h-0 flex-1 overflow-y-auto text-sm" aria-label="Sessions">
          {(sessions.data ?? []).map((s) => (
            <li key={s.id}>
              <Link
                to="/assistant/$sessionId"
                params={{ sessionId: s.id }}
                className="block border-t border-line px-3 py-2 hover:bg-paper [&.active]:bg-paper"
              >
                <span className="line-clamp-2">{s.request}</span>
                <span className="text-xs text-muted">{STATUS_LABEL[s.status]}</span>
              </Link>
            </li>
          ))}
        </ul>
      </aside>
      <main className="min-h-0 overflow-y-auto">
        {sessionId ? (
          <SessionView key={sessionId} sessionId={sessionId} />
        ) : (
          <NewSession runId={runId} />
        )}
      </main>
    </div>
  )
}

function NewSession({ runId }: { runId?: string }) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [message, setMessage] = useState('')
  const [region, setRegion] = useState('')
  const start = useMutation({
    mutationFn: () =>
      postJson<AgentSession>('/agent/sessions', {
        message,
        region_id: region || null,
        run_id: runId ?? null,
      }),
    onSuccess: (s) => {
      void queryClient.invalidateQueries({ queryKey: ['agent-sessions'] })
      void navigate({ to: '/assistant/$sessionId', params: { sessionId: s.id } })
    },
  })
  return (
    <form
      className="mx-auto flex max-w-2xl flex-col gap-3 p-6"
      onSubmit={(e) => {
        e.preventDefault()
        start.mutate()
      }}
    >
      <h1 className="text-lg font-semibold">Ask the planning assistant</h1>
      <p className="text-sm text-muted">
        Ask about chargers, demand, policy or an existing plan, or ask for a new network. Planning
        requests stop for your confirmation before the optimisation runs. Every number in the answer
        is checked against the platform's own results.
      </p>
      {runId && (
        <p className="text-sm">
          About run <code className="text-xs">{runId.slice(0, 8)}</code>
        </p>
      )}
      <label className="flex flex-col gap-1 text-sm">
        Your question
        <textarea
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          rows={3}
          className="rounded border border-line bg-panel p-2"
          placeholder={EXAMPLES[0]}
        />
      </label>
      <div className="flex flex-wrap gap-2">
        {EXAMPLES.map((ex) => (
          <button
            key={ex}
            type="button"
            onClick={() => setMessage(ex)}
            className="rounded border border-line px-2 py-1 text-left text-xs text-muted hover:text-ink"
          >
            {ex}
          </button>
        ))}
      </div>
      <div className="flex items-center gap-3">
        <label className="flex items-center gap-2 text-sm">
          Region
          <select
            value={region}
            onChange={(e) => setRegion(e.target.value)}
            className="rounded border border-line bg-panel px-2 py-1"
          >
            <option value="">Let the planner choose</option>
            <option value="pmr">Pune Metropolitan Region</option>
            <option value="mumbai_pune">Mumbai–Pune corridor</option>
          </select>
        </label>
        <button
          type="submit"
          disabled={message.trim().length < 3 || start.isPending}
          className="ml-auto rounded bg-ink px-3 py-1.5 text-sm text-paper disabled:opacity-50"
        >
          {start.isPending ? 'Starting…' : 'Ask'}
        </button>
      </div>
      {start.error && (
        <p role="alert" className="text-sm text-warn">
          {start.error.message}
        </p>
      )}
    </form>
  )
}

function useAgentEvents(sessionId: string, status: SessionStatus | undefined): AgentEvent[] {
  const [events, setEvents] = useState<AgentEvent[]>([])
  const last = useRef(0)
  useEffect(() => {
    if (!status) return
    const source = new EventSource(
      streamUrl(`/agent/sessions/${sessionId}/events?after=${last.current}`),
    )
    const onEvent = (e: Event) => {
      if (!(e instanceof MessageEvent) || !e.data) {
        source.close() // connection error, not a server-sent `error` event
        return
      }
      const id = Number(e.lastEventId)
      if (e.type === 'status') {
        source.close()
        return
      }
      if (id && id <= last.current) return
      if (id) last.current = id
      const data = JSON.parse(e.data as string) as Record<string, unknown>
      setEvents((prev) => [
        ...prev,
        { id, type: e.type, agent: (data.agent as string) ?? null, data },
      ])
    }
    for (const t of EVENT_TYPES) source.addEventListener(t, onEvent)
    return () => source.close()
  }, [sessionId, status])
  return events
}

function SessionView({ sessionId }: { sessionId: string }) {
  const queryClient = useQueryClient()
  const session = useQuery({
    queryKey: ['agent-session', sessionId],
    queryFn: () => getJson<AgentSession>(`/agent/sessions/${sessionId}`),
    refetchInterval: (q) => (q.state.data?.status === 'running' ? 2000 : false),
  })
  const s = session.data
  const events = useAgentEvents(sessionId, s?.status)
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['agent-session', sessionId] })
    void queryClient.invalidateQueries({ queryKey: ['agent-sessions'] })
  }
  const act = useMutation({
    mutationFn: ({ path, body }: { path: string; body: unknown }) =>
      postJson<AgentSession>(`/agent/sessions/${sessionId}/${path}`, body),
    onSuccess: refresh,
  })
  if (!s) return <p className="p-6 text-sm text-muted">Loading…</p>

  const planEvent = events.findLast((e) => e.type === 'plan')
  const plan = (planEvent?.data as unknown as AgentPlan | undefined) ?? s.summary.plan
  const steps = timeline(events)
  if (s.status !== 'running') steps.forEach((st) => (st.done = true))
  const progress = runProgress(events)
  const report = s.status === 'completed' ? s.summary.report : undefined
  const draft = streamedText(events)
  const errors = events.filter((e) => e.type === 'error' && e.data.message)

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-4 p-6">
      <div className="self-end rounded bg-paper px-3 py-2 text-sm">{s.request}</div>
      {plan && <PlanCard plan={plan} />}
      {steps.length > 0 && (
        <section aria-label="Agent steps" className="flex flex-col gap-2">
          {steps.map((st, i) => (
            <div key={i} className="rounded border border-line bg-panel px-3 py-2 text-sm">
              <div className="flex items-center gap-2">
                <span aria-hidden className={st.done ? 'text-muted' : 'animate-pulse'}>
                  {st.done ? '✓' : '●'}
                </span>
                <span className="font-medium">{agentLabel(st.agent)}</span>
                <span className="text-muted">{st.goal}</span>
              </div>
              {st.tools.length > 0 && (
                <ul className="mt-1 flex flex-col gap-0.5 pl-6 text-xs">
                  {st.tools.map((t, j) => (
                    <li key={j} className={t.isError ? 'text-warn' : 'text-muted'}>
                      <code>{t.tool}</code>
                      {t.summary !== null ? ` — ${t.summary}` : ' …'}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          ))}
        </section>
      )}
      {progress.length > 0 && s.status === 'running' && (
        <p className="text-xs text-muted" aria-label="Planning run progress">
          Planning run:{' '}
          {progress.map((p) => `${p.step} ${p.status === 'completed' ? '✓' : '…'}`).join(' · ')}
        </p>
      )}
      {s.status === 'awaiting_confirmation' && s.summary.confirmation && (
        <ConfirmCard
          c={s.summary.confirmation}
          busy={act.isPending}
          onDecide={(approved, note) => act.mutate({ path: 'confirm', body: { approved, note } })}
        />
      )}
      {report ? (
        <ReportCard report={report} />
      ) : (
        draft &&
        s.status === 'running' && (
          <section className="rounded border border-dashed border-line p-3 opacity-80">
            <p className="mb-1 text-xs text-muted">
              Drafting — numbers are checked before the answer is final
            </p>
            <Markdown text={draft} />
          </section>
        )
      )}
      {(s.error || errors.length > 0) && (
        <p role="alert" className="text-sm text-warn">
          {s.error ?? String(errors.at(-1)?.data.message)}
        </p>
      )}
      <footer className="flex flex-wrap items-center gap-3 border-t border-line pt-2 text-xs text-muted">
        <span>{STATUS_LABEL[s.status]}</span>
        <span>
          {s.llm_calls} model calls · {s.tool_calls} tool calls · ${s.cost_usd.toFixed(2)}
        </span>
        {s.status === 'running' && (
          <button
            type="button"
            className="underline"
            onClick={() => act.mutate({ path: 'cancel', body: {} })}
          >
            Cancel
          </button>
        )}
        {s.status === 'interrupted' && (
          <button
            type="button"
            className="underline"
            onClick={() => act.mutate({ path: 'resume', body: {} })}
          >
            Resume
          </button>
        )}
      </footer>
      {(s.status === 'completed' || s.status === 'stopped') && (
        <FollowUp
          busy={act.isPending}
          onAsk={(message) => act.mutate({ path: 'messages', body: { message } })}
        />
      )}
      {act.error && (
        <p role="alert" className="text-sm text-warn">
          {act.error.message}
        </p>
      )}
    </div>
  )
}

function PlanCard({ plan }: { plan: AgentPlan }) {
  const sc = plan.scenario as Record<string, unknown> | null
  return (
    <section aria-label="Plan" className="rounded border border-line bg-panel px-3 py-2 text-sm">
      <p className="font-medium">{plan.restated_request}</p>
      {plan.intent === 'plan' && sc && (
        <p className="mt-1 text-xs text-muted">
          {String(sc.region_id ?? '')} · {String(sc.target_year)} ·{' '}
          {(sc.charger_classes as string[]).join(', ')}
          {typeof sc.budget_inr === 'number' && ` · budget ${formatCrore(sc.budget_inr)}`}
          {typeof sc.max_sites === 'number' && ` · up to ${sc.max_sites} sites`}
          {sc.weight_profile ? ` · ${String(sc.weight_profile)} profile` : ''}
        </p>
      )}
      {plan.steps.length > 0 && (
        <ol className="mt-2 list-decimal pl-5 text-xs">
          {plan.steps.map((st, i) => (
            <li key={i}>
              <span className="font-medium">{agentLabel(st.agent)}</span> — {st.goal}
            </li>
          ))}
        </ol>
      )}
      {plan.assumptions.length > 0 && (
        <p className="mt-2 text-xs text-muted">Assumed: {plan.assumptions.join('; ')}</p>
      )}
    </section>
  )
}

function ConfirmCard({
  c,
  busy,
  onDecide,
}: {
  c: Confirmation
  busy: boolean
  onDecide: (approved: boolean, note: string | null) => void
}) {
  const [note, setNote] = useState('')
  return (
    <section aria-label="Confirmation" className="rounded border-2 border-ink px-3 py-3 text-sm">
      <p className="font-medium">{c.question}</p>
      <ul className="mt-2 flex flex-col gap-1 text-xs">
        {c.findings
          .filter((f) => f.summary)
          .map((f, i) => (
            <li key={i}>
              <span className="font-medium">{agentLabel(f.agent)}:</span> {f.summary}
            </li>
          ))}
      </ul>
      {c.questions_for_user.length > 0 && (
        <div className="mt-2 text-xs">
          <p className="font-medium">Open questions</p>
          <ul className="list-disc pl-5">
            {c.questions_for_user.map((q) => (
              <li key={q}>{q}</li>
            ))}
          </ul>
        </div>
      )}
      <p className="mt-2 text-xs text-muted">
        Cost so far ${c.cost.llm_usd_so_far.toFixed(2)} in model calls; about $
        {c.cost.llm_usd_estimate_remaining.toFixed(2)} more. Paid map APIs: ₹{c.cost.paid_api_inr}.{' '}
        {c.cost.compute}.
      </p>
      <label className="mt-2 flex flex-col gap-1 text-xs">
        Note for the agents (optional)
        <input
          value={note}
          onChange={(e) => setNote(e.target.value)}
          className="rounded border border-line bg-panel px-2 py-1"
          placeholder="e.g. e-4W only"
        />
      </label>
      <div className="mt-3 flex gap-2">
        <button
          type="button"
          disabled={busy}
          onClick={() => onDecide(true, note || null)}
          className="rounded bg-ink px-3 py-1.5 text-paper disabled:opacity-50"
        >
          Run optimisation
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => onDecide(false, note || null)}
          className="rounded border border-line px-3 py-1.5 disabled:opacity-50"
        >
          Stop here
        </button>
        {c.run_id && (
          <Link
            to="/runs/$runId"
            params={{ runId: c.run_id }}
            className="ml-auto self-center text-xs underline"
          >
            See the candidates
          </Link>
        )}
      </div>
    </section>
  )
}

function ReportCard({ report }: { report: AgentReport }) {
  const v = report.validation
  return (
    <section aria-label="Answer" className="rounded border border-line bg-panel px-4 py-3">
      <Markdown text={report.markdown} />
      <div className="mt-3 flex flex-wrap items-center gap-3 border-t border-line pt-2 text-xs text-muted">
        <span>
          {v.numbers_checked} numbers checked against tool results
          {v.regenerations > 0 && ` · redrafted ${v.regenerations}×`}
        </span>
        {report.evidence_ids.length > 0 && (
          <span>{report.evidence_ids.length} evidence records</span>
        )}
        {report.links.run_id && (
          <Link to="/runs/$runId" params={{ runId: report.links.run_id }} className="underline">
            Open the plan on the map
          </Link>
        )}
      </div>
      {v.removed_sentences.length > 0 && (
        <details className="mt-2 text-xs text-warn">
          <summary>
            {v.removed_sentences.length} sentence(s) removed: their numbers matched no result
          </summary>
          <ul className="list-disc pl-5">
            {v.removed_sentences.map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
        </details>
      )}
    </section>
  )
}

function FollowUp({ busy, onAsk }: { busy: boolean; onAsk: (m: string) => void }) {
  const [message, setMessage] = useState('')
  return (
    <form
      className="flex gap-2"
      onSubmit={(e) => {
        e.preventDefault()
        onAsk(message)
        setMessage('')
      }}
    >
      <input
        value={message}
        onChange={(e) => setMessage(e.target.value)}
        className="flex-1 rounded border border-line bg-panel px-2 py-1 text-sm"
        placeholder="Ask a follow-up about this answer or plan"
        aria-label="Follow-up question"
      />
      <button
        type="submit"
        disabled={busy || message.trim().length < 3}
        className="rounded bg-ink px-3 py-1 text-sm text-paper disabled:opacity-50"
      >
        Ask
      </button>
    </form>
  )
}

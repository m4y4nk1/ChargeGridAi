/** Agent chat (Section 10.4 SSE events) types and helpers. */

export type SessionStatus =
  | 'running'
  | 'awaiting_confirmation'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'stopped'
  | 'interrupted'

export interface PlanStep {
  agent: string
  goal: string
}

export interface AgentPlan {
  intent: 'plan' | 'question'
  restated_request: string
  scenario: Record<string, unknown> | null
  steps: PlanStep[]
  consult: string[]
  assumptions: string[]
  questions_for_user: string[]
}

export interface Confirmation {
  question: string
  scenario: Record<string, unknown> | null
  run_id: string | null
  findings: { agent: string; summary: string }[]
  questions_for_user: string[]
  cost: {
    llm_usd_so_far: number
    llm_usd_estimate_remaining: number
    paid_api_inr: number
    compute: string
  }
}

export interface AgentReport {
  markdown: string
  validation: {
    numbers_checked: number
    unmatched_after: number
    regenerations: number
    removed_sentences: string[]
  }
  evidence_ids: string[]
  links: { run_id: string | null; optimisation_id: string | null; scenario_id: string | null }
}

export interface AgentSession {
  id: string
  status: SessionStatus
  request: string
  region_id: string | null
  run_id: string | null
  scenario_id: string | null
  summary: { plan?: AgentPlan; confirmation?: Confirmation; report?: AgentReport }
  cost_usd: number
  input_tokens: number
  output_tokens: number
  llm_calls: number
  tool_calls: number
  error: string | null
  created_at: string
  updated_at: string
}

export interface AgentEvent {
  id: number
  type: string
  agent: string | null
  data: Record<string, unknown>
}

export const EVENT_TYPES = [
  'plan',
  'step_started',
  'tool_call',
  'tool_result_summary',
  'progress',
  'partial_text',
  'evidence',
  'needs_confirmation',
  'error',
  'completed',
  'user_message',
  'status',
] as const

export const AGENT_LABELS: Record<string, string> = {
  planner: 'Planner',
  data_discovery: 'Data Discovery',
  geo: 'Geo',
  demand: 'Demand',
  infrastructure: 'Infrastructure',
  grid_energy: 'Grid & Energy',
  finance: 'Finance',
  report: 'Report',
}

export const agentLabel = (a: string | null) => (a ? (AGENT_LABELS[a] ?? a) : 'System')

export const isActive = (s: SessionStatus) => s === 'running'

/** One visible step: an agent's goal with the tool calls it made. */
export interface TimelineStep {
  agent: string | null
  goal: string
  tools: { tool: string; summary: string | null; isError: boolean }[]
  done: boolean
  summary: string | null
}

/** Group events into agent steps; events before a follow-up question start a new turn. */
export function timeline(events: AgentEvent[]): TimelineStep[] {
  const steps: TimelineStep[] = []
  const current = () => steps.at(-1)
  for (const e of events) {
    if (e.type === 'user_message') steps.length = 0
    else if (e.type === 'step_started')
      steps.push({
        agent: e.agent,
        goal: String(e.data.goal ?? ''),
        tools: [],
        done: false,
        summary: null,
      })
    else if (e.type === 'tool_call' && current())
      current()!.tools.push({ tool: String(e.data.tool), summary: null, isError: false })
    else if (e.type === 'tool_result_summary') {
      const t = current()?.tools.findLast((x) => x.tool === e.data.tool && x.summary === null)
      if (t) {
        t.summary = String(e.data.summary ?? '')
        t.isError = Boolean(e.data.is_error)
      }
    } else if (
      e.type === 'progress' &&
      e.data.status === 'completed' &&
      e.agent &&
      e.data.step === e.agent
    ) {
      const s = steps.findLast((x) => x.agent === e.agent)
      if (s) {
        s.done = true
        s.summary = typeof e.data.summary === 'string' ? e.data.summary : null
      }
    }
  }
  steps.forEach((s, i) => {
    if (i < steps.length - 1) s.done = true
  })
  return steps
}

/** The report text streamed so far (a `reset` starts a regenerated draft). */
export function streamedText(events: AgentEvent[]): string {
  let text = ''
  for (const e of events) {
    if (e.type === 'user_message') text = ''
    if (e.type !== 'partial_text') continue
    if (e.data.reset) text = ''
    text += String(e.data.text ?? '')
  }
  return text
}

/** Run progress relayed from the planning run (step name -> latest status). */
export function runProgress(events: AgentEvent[]): { step: string; status: string }[] {
  const latest = new Map<string, string>()
  for (const e of events) {
    if (e.type === 'user_message') latest.clear()
    if (e.type === 'progress' && e.data.run_id && typeof e.data.step === 'string')
      latest.set(e.data.step, String(e.data.status))
  }
  return [...latest].filter(([s]) => s !== 'run').map(([step, status]) => ({ step, status }))
}

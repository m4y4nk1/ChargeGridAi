import { describe, expect, it } from 'vitest'
import { type AgentEvent, runProgress, streamedText, timeline } from './agents'

let n = 0
const ev = (type: string, agent: string | null, data: Record<string, unknown>): AgentEvent => ({
  id: ++n,
  type,
  agent,
  data,
})

describe('agent event helpers', () => {
  const events = [
    ev('plan', 'planner', { intent: 'plan' }),
    ev('step_started', 'data_discovery', { goal: 'check data' }),
    ev('tool_call', 'data_discovery', { tool: 'catalog.freshness' }),
    ev('tool_result_summary', 'data_discovery', {
      tool: 'catalog.freshness',
      summary: '2 gaps',
      is_error: false,
    }),
    ev('progress', 'data_discovery', {
      step: 'data_discovery',
      status: 'completed',
      summary: 'ok',
    }),
    ev('step_started', 'planner', { goal: 'run to scoring' }),
    ev('progress', null, { run_id: 'r', step: 'candidates', status: 'started' }),
    ev('progress', null, { run_id: 'r', step: 'candidates', status: 'completed' }),
    ev('progress', null, { run_id: 'r', step: 'run', status: 'completed' }),
    ev('step_started', 'report', { goal: 'write' }),
    ev('partial_text', 'report', { text: 'Draft with ₹987 crore' }),
    ev('partial_text', 'report', { text: '', reset: true }),
    ev('partial_text', 'report', { text: 'Final ' }),
    ev('partial_text', 'report', { text: 'answer' }),
  ]

  it('groups steps with their tool calls', () => {
    const steps = timeline(events)
    expect(steps.map((s) => s.agent)).toEqual(['data_discovery', 'planner', 'report'])
    expect(steps[0]).toMatchObject({
      done: true,
      summary: 'ok',
      tools: [{ tool: 'catalog.freshness', summary: '2 gaps' }],
    })
    expect(steps[2].done).toBe(false)
  })

  it('keeps only the latest draft after a regeneration', () => {
    expect(streamedText(events)).toBe('Final answer')
  })

  it('reports planning-run progress without the run marker', () => {
    expect(runProgress(events)).toEqual([{ step: 'candidates', status: 'completed' }])
  })

  it('starts a fresh timeline on a follow-up question', () => {
    const more = [
      ...events,
      ev('user_message', null, { message: 'and?' }),
      ev('step_started', 'planner', { goal: 'x' }),
    ]
    expect(timeline(more).map((s) => s.agent)).toEqual(['planner'])
    expect(streamedText(more)).toBe('')
  })
})

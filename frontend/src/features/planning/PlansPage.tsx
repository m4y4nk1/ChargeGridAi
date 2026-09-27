import { useMutation, useQuery } from '@tanstack/react-query'
import { Link, useNavigate } from '@tanstack/react-router'
import { useState } from 'react'
import {
  CHARGER_CLASSES,
  getJson,
  postJson,
  type ChargerClass,
  type PlanningRun,
  type Scenario,
  type ScenarioInput,
  type WeightProfile,
} from '../../lib/api'
import { formatCount, formatDate } from '../../lib/format'
import { parseUserSites } from '../../lib/planning'

const YEARS = Array.from({ length: 10 }, (_, i) => 2026 + i)
const CASES = [
  { id: 'slow', label: 'Slow adoption' },
  { id: 'base', label: 'Base' },
  { id: 'fast', label: 'Fast adoption' },
] as const

const field = 'mt-1 rounded border border-line bg-panel px-2 py-1 text-sm text-ink'

export function ScenarioForm({ onCreated }: { onCreated: (run: PlanningRun) => void }) {
  const [name, setName] = useState('')
  const [year, setYear] = useState(2030)
  const [adoption, setAdoption] = useState<ScenarioInput['adoption_case']>('base')
  const [classes, setClasses] = useState<ChargerClass[]>(['DC_60'])
  const [sitesText, setSitesText] = useState('')
  const [profile, setProfile] = useState<string>('')
  const [budgetCr, setBudgetCr] = useState('')
  const [maxSites, setMaxSites] = useState('')
  const profiles = useQuery({
    queryKey: ['weight-profiles'],
    queryFn: () => getJson<WeightProfile[]>('/weight-profiles'),
  })
  const parsed = parseUserSites(sitesText)

  const start = useMutation({
    mutationFn: async () => {
      const scenario = await postJson<Scenario>('/scenarios', {
        name: name.trim() || `Pune ${year} ${adoption}`,
        target_year: year,
        adoption_case: adoption,
        charger_classes: classes,
        weight_profile: profile || null,
        budget_inr: budgetCr ? Math.round(Number(budgetCr) * 1e7) : null,
        max_sites: maxSites ? Number(maxSites) : null,
        user_sites: parsed.sites,
      } satisfies ScenarioInput)
      return postJson<PlanningRun>('/runs', { scenario_id: scenario.id })
    },
    onSuccess: onCreated,
  })

  const toggle = (c: ChargerClass) =>
    setClasses((cur) => (cur.includes(c) ? cur.filter((x) => x !== c) : [...cur, c]))
  const badNumber =
    (budgetCr !== '' && !(Number(budgetCr) > 0)) ||
    (maxSites !== '' && !(Number.isInteger(Number(maxSites)) && Number(maxSites) > 0))
  const blocked = classes.length === 0 || parsed.errors.length > 0 || badNumber || start.isPending

  return (
    <form
      aria-label="New planning run"
      className="flex flex-col gap-4 rounded border border-line bg-panel p-4"
      onSubmit={(e) => {
        e.preventDefault()
        if (!blocked) start.mutate()
      }}
    >
      <h2 className="text-sm font-semibold">New planning run</h2>
      <label className="flex flex-col text-xs text-muted">
        Name
        <input
          className={field}
          value={name}
          placeholder={`Pune ${year} ${adoption}`}
          onChange={(e) => setName(e.target.value)}
          maxLength={200}
        />
      </label>
      <div className="flex flex-wrap gap-4">
        <label className="flex flex-col text-xs text-muted">
          Target year
          <select className={field} value={year} onChange={(e) => setYear(Number(e.target.value))}>
            {YEARS.map((y) => (
              <option key={y} value={y}>
                {y}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col text-xs text-muted">
          Adoption
          <select
            className={field}
            value={adoption}
            onChange={(e) => setAdoption(e.target.value as ScenarioInput['adoption_case'])}
          >
            {CASES.map((c) => (
              <option key={c.id} value={c.id}>
                {c.label}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col text-xs text-muted">
          Weight profile
          <select className={field} value={profile} onChange={(e) => setProfile(e.target.value)}>
            <option value="">Default (CPO commercial)</option>
            {profiles.data?.map((p) => (
              <option key={p.name} value={p.name}>
                {p.label}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="flex flex-wrap gap-4">
        <label className="flex flex-col text-xs text-muted">
          Budget (₹ crore, optional)
          <input
            className={field}
            inputMode="decimal"
            placeholder="10"
            value={budgetCr}
            onChange={(e) => setBudgetCr(e.target.value)}
          />
        </label>
        <label className="flex flex-col text-xs text-muted">
          Max sites
          <input
            className={field}
            inputMode="numeric"
            placeholder="20"
            value={maxSites}
            onChange={(e) => setMaxSites(e.target.value)}
          />
        </label>
      </div>
      <p className="-mt-2 text-[11px] text-muted">
        With a budget the run also optimises: which sites to build, with how many chargers.
      </p>
      <fieldset className="text-xs text-muted">
        <legend>Charger classes</legend>
        <div className="mt-1 flex flex-wrap gap-2">
          {CHARGER_CLASSES.map((c) => (
            <label key={c} className="flex items-center gap-1 text-sm text-ink">
              <input type="checkbox" checked={classes.includes(c)} onChange={() => toggle(c)} />
              {c.replace('_', ' ')} kW
            </label>
          ))}
        </div>
        {classes.length === 0 && <p className="mt-1 text-warn">Pick at least one class.</p>}
      </fieldset>
      <label className="flex flex-col text-xs text-muted">
        Your candidate sites (optional) — one per line: lat, lng, name
        <textarea
          className={`${field} h-20 font-mono text-xs`}
          value={sitesText}
          placeholder="18.5308, 73.8475, Shivajinagar depot"
          onChange={(e) => setSitesText(e.target.value)}
        />
      </label>
      {parsed.errors.map((err) => (
        <p key={err} className="text-xs text-warn">
          {err}
        </p>
      ))}
      {start.isError && <p className="text-xs text-warn">{start.error.message}</p>}
      <button
        type="submit"
        disabled={blocked}
        className="self-start rounded bg-ink px-3 py-1.5 text-sm text-paper disabled:opacity-40"
      >
        {start.isPending ? 'Starting…' : 'Generate candidates'}
      </button>
    </form>
  )
}

const STATUS_STYLE: Record<string, string> = {
  succeeded: 'text-ink',
  failed: 'text-warn',
  running: 'text-muted',
  queued: 'text-muted',
}

/** Planning runs (Section 3, module B entry point): scenario form and run history. */
export function PlansPage() {
  const navigate = useNavigate()
  const runs = useQuery({
    queryKey: ['runs'],
    queryFn: () => getJson<PlanningRun[]>('/runs'),
  })

  return (
    <div className="h-full overflow-y-auto">
      <main className="mx-auto grid max-w-6xl gap-5 p-6 md:grid-cols-[minmax(0,420px)_1fr]">
        <div className="flex flex-col gap-3">
          <div>
            <h1 className="text-xl font-semibold">Site planning</h1>
            <p className="text-sm text-muted">
              Generate candidate sites and screen them against feasibility rules. Every rejection
              records its rule and the measurement behind it.
            </p>
          </div>
          <ScenarioForm
            onCreated={(run) => navigate({ to: '/runs/$runId', params: { runId: run.id } })}
          />
        </div>
        <section className="rounded border border-line bg-panel p-4">
          <h2 className="mb-2 text-sm font-semibold">Recent runs</h2>
          {runs.isError && <p className="text-sm text-warn">Couldn’t load runs.</p>}
          {runs.data?.length === 0 && <p className="text-sm text-muted">No runs yet.</p>}
          <table className="w-full text-sm">
            <tbody>
              {runs.data?.map((r) => (
                <tr key={r.id} className="border-t border-line first:border-0">
                  <td className="py-1.5">
                    <Link to="/runs/$runId" params={{ runId: r.id }} className="underline">
                      {r.scenario.name}
                    </Link>
                    <div className="text-xs text-muted">
                      {r.scenario.target_year} · {r.scenario.adoption_case} ·{' '}
                      {r.scenario.charger_classes.join(', ')}
                    </div>
                  </td>
                  <td className={`py-1.5 text-xs ${STATUS_STYLE[r.status] ?? ''}`}>{r.status}</td>
                  <td className="py-1.5 text-right text-xs tabular-nums text-muted">
                    {r.funnel.feasible
                      ? `${formatCount(r.funnel.feasible.total)} feasible of ${formatCount(r.funnel.candidates?.total)}`
                      : '—'}
                  </td>
                  <td className="py-1.5 text-right text-xs text-muted">
                    {formatDate(r.created_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      </main>
    </div>
  )
}

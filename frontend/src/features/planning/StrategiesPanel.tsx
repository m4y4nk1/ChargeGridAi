import { useQuery } from '@tanstack/react-query'
import ReactECharts from 'echarts-for-react'
import { getJson, type OptimisationSummary } from '../../lib/api'
import { formatCrore, formatPercent } from '../../lib/format'
import { identicalPlans, siteTitle } from '../../lib/planning'

const STRATEGY_ORDER = ['max_coverage', 'equity', 'cost', 'commercial']

function StrategyCard({ o }: { o: OptimisationSummary }) {
  const k = o.kpis
  return (
    <article className="flex flex-col gap-2 rounded border border-line bg-panel p-3 text-sm">
      <h3 className="font-semibold">{o.label}</h3>
      <dl className="grid grid-cols-2 gap-x-3 gap-y-1">
        <dt className="text-muted">Demand served</dt>
        <dd className="text-right tabular-nums">{formatPercent(k.coverage_share)}</dd>
        <dt className="text-muted">Underserved reached</dt>
        <dd className="text-right tabular-nums">{formatPercent(k.equity)}</dd>
        <dt className="text-muted">Cost</dt>
        <dd className="text-right tabular-nums">{formatCrore(k.cost_inr)}</dd>
        <dt className="text-muted">Stations</dt>
        <dd className="text-right tabular-nums">{k.n_sites}</dd>
        <dt className="text-muted">Charge points</dt>
        <dd className="text-right tabular-nums">{k.n_charge_points}</dd>
        <dt className="text-muted">Utilisation</dt>
        <dd className="text-right tabular-nums">{formatPercent(k.utilisation)}</dd>
      </dl>
      <p className="text-xs text-muted">
        Mix:{' '}
        {Object.entries(k.charger_mix)
          .map(([c, n]) => `${n}× ${c.replace('_', ' ')} kW`)
          .join(', ') || '—'}
      </p>
      <div>
        <h4 className="text-xs font-semibold uppercase tracking-wide text-muted">
          Top 5 locations
        </h4>
        <ol className="mt-1 list-decimal pl-4 text-xs">
          {k.top_sites.map((s) => (
            <li key={s.site_id}>{siteTitle(s)}</li>
          ))}
        </ol>
      </div>
      <p className="text-[11px] text-muted">
        {o.solver_stats.solver} · {o.status}
        {o.solver_stats.mip_gap !== null && ` · gap ${formatPercent(o.solver_stats.mip_gap, 2)}`}
      </p>
    </article>
  )
}

/** Scenario Comparison (module C): named strategies side by side and the Pareto front. */
export function StrategiesPanel({ runId }: { runId: string }) {
  const opts = useQuery({
    queryKey: ['optimisations', runId],
    queryFn: () => getJson<OptimisationSummary[]>(`/runs/${runId}/optimisations`),
  })
  if (opts.isError) return <p className="p-4 text-sm text-warn">Couldn’t load strategies.</p>
  if (!opts.data) return <p className="p-4 text-sm text-muted">Loading…</p>
  const strategies = opts.data
    .filter((o) => o.kind === 'strategy')
    .sort((a, b) => STRATEGY_ORDER.indexOf(a.strategy) - STRATEGY_ORDER.indexOf(b.strategy))
  const points = opts.data.filter(
    (o) => (o.kind === 'strategy' || o.kind === 'pareto') && o.kpis.cost_inr,
  )
  if (strategies.length === 0) {
    return <p className="p-4 text-sm text-muted">No strategies for this run (set a budget).</p>
  }

  return (
    <div className="h-full overflow-y-auto p-4">
      <h2 className="text-lg font-semibold">Scenario comparison</h2>
      <p className="mb-3 text-sm text-muted">
        Each strategy re-optimises the same candidates with a different objective at the scenario
        budget. Costs are illustrative and demand is synthetic.
      </p>
      {identicalPlans(strategies).map((group) => (
        <p key={group.join()} className="mb-3 rounded border border-line bg-panel p-2 text-xs">
          <b>{group.join(', ')}</b> chose the same plan. Unmet demand exceeds what any plan at this
          budget can serve, so every charger fills (utilisation 100%) and valuing kWh, score or
          revenue ranks sites the same way. They diverge once demand is scarce.
        </p>
      ))}
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        {strategies.map((o) => (
          <StrategyCard key={o.id} o={o} />
        ))}
      </div>
      <section aria-label="Pareto chart" className="mt-5 rounded border border-line bg-panel p-3">
        <h3 className="text-sm font-semibold">
          Cost vs demand served · colour = underserved reached
        </h3>
        <p className="text-xs text-muted">
          Every strategy at 50–200% of the budget. Larger points are on the Pareto front: no other
          plan serves more, reaches more underserved people and costs less at once.
        </p>
        <ReactECharts
          style={{ height: 360 }}
          option={{
            animation: false,
            grid: { left: 56, right: 90, top: 16, bottom: 44 },
            xAxis: { type: 'value', name: 'Cost (₹ Cr)', nameLocation: 'middle', nameGap: 28 },
            yAxis: {
              type: 'value',
              name: 'Unmet demand served (%)',
              nameLocation: 'middle',
              nameGap: 40,
            },
            visualMap: {
              dimension: 2,
              min: 0,
              max: 100,
              right: 0,
              top: 'middle',
              text: ['more', 'fewer'],
              calculable: false,
              inRange: { color: ['#fde725', '#21918c', '#440154'] },
            },
            tooltip: {
              formatter: (p: { data: [number, number, number, string, boolean] }) =>
                `${p.data[3]}<br/>₹${p.data[0].toFixed(2)} Cr · ${p.data[1].toFixed(1)}% served · ${p.data[2].toFixed(1)}% underserved reached${p.data[4] ? '<br/>on the front' : ''}`,
            },
            series: [
              {
                type: 'scatter',
                symbolSize: (d: [number, number, number, string, boolean]) => (d[4] ? 16 : 8),
                data: points.map((o) => [
                  o.kpis.cost_inr / 1e7,
                  (o.kpis.coverage_share ?? 0) * 100,
                  (o.kpis.equity ?? 0) * 100,
                  `${o.params.label ?? o.label} · ${Math.round((o.params.budget_share ?? 1) * 100)}% budget`,
                  o.on_front,
                ]),
              },
            ],
          }}
        />
      </section>
    </div>
  )
}

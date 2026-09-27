import { useQuery } from '@tanstack/react-query'
import ReactECharts from 'echarts-for-react'
import { useState } from 'react'
import { useChartColors, type ChartPalette } from '../../components/charts/chartColors'
import { getJson, SEGMENT_LABELS, type Quantiles, type SiteEconomics } from '../../lib/api'
import { formatCount, formatCrore, formatLakh, formatPercent } from '../../lib/format'

const h4 = 'text-xs font-semibold uppercase tracking-wide text-muted'

function Stat({ label, value, detail }: { label: string; value: string; detail?: string }) {
  return (
    <div className="rounded border border-line p-2">
      <div className="text-[11px] text-muted">{label}</div>
      <div className="text-base font-semibold tabular-nums">{value}</div>
      {detail && <div className="text-[11px] text-muted">{detail}</div>}
    </div>
  )
}

function axis(c: ChartPalette) {
  return {
    axisLine: { lineStyle: { color: c.grid } },
    axisTick: { show: false },
    axisLabel: { color: c.muted, fontSize: 10 },
    splitLine: { lineStyle: { color: c.grid } },
  }
}

const HOURS = Array.from({ length: 24 }, (_, h) => String(h).padStart(2, '0'))

function HourlyChart({
  title,
  values,
  format,
  peak,
  c,
}: {
  title: string
  values: number[]
  format: (v: number) => string
  peak: number
  c: ChartPalette
}) {
  return (
    <figure aria-label={title}>
      <figcaption className="text-xs text-muted">{title}</figcaption>
      <ReactECharts
        style={{ height: 120 }}
        option={{
          animation: false,
          grid: { left: 36, right: 8, top: 8, bottom: 20 },
          tooltip: {
            trigger: 'axis',
            axisPointer: { type: 'shadow' },
            valueFormatter: (v: number) => format(v),
          },
          xAxis: {
            type: 'category',
            data: HOURS,
            ...axis(c),
            axisLabel: { ...axis(c).axisLabel, interval: 5 },
          },
          yAxis: {
            type: 'value',
            ...axis(c),
            axisLabel: { ...axis(c).axisLabel, formatter: format },
          },
          series: [
            {
              type: 'bar',
              barCategoryGap: '20%',
              data: values.map((v, h) => ({
                value: v,
                itemStyle: {
                  color: h === peak ? c.warm : c.cool,
                  borderRadius: [4, 4, 0, 0],
                },
              })),
            },
          ],
        }}
      />
    </figure>
  )
}

/** Sizing tab (Section 9.8): charger count, queue metrics, connectors, electrical. */
function SizingView({ data }: { data: SiteEconomics }) {
  const c = useChartColors()
  const s = data.sizing
  const sim = s.simulation
  const band = s.targets.utilisation_band
  return (
    <div className="flex flex-col gap-4 text-sm">
      <div>
        <p className="text-base font-semibold">
          {s.chargers} × {s.charger_class.replace('_', ' ')} kW recommended
        </p>
        <p className="text-xs text-muted">
          Plan bundle: {s.plan_bundle.bundle.replace('_', ' ').replace('x', ' kW × ')} · serving{' '}
          {formatCount(Math.round(s.served_kwh_per_day))} kWh/day as{' '}
          {formatCount(Math.round(s.sessions_per_day))} sessions ({s.mean_kwh_per_session} kWh,{' '}
          {s.mean_service_minutes} min each on average)
        </p>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <Stat
          label={`Wait > ${s.targets.max_wait_minutes} min, peak hour`}
          value={`${formatPercent(sim.prob_wait_over_peak_hour)} simulated`}
          detail={`Erlang-C ${formatPercent(s.erlang.prob_wait_over)} · target ≤ ${formatPercent(s.targets.max_prob_wait, 0)}`}
        />
        <Stat
          label="Mean wait (simulated)"
          value={`${sim.mean_wait_min.toFixed(1)} min`}
          detail={`95th percentile ${sim.p95_wait_min.toFixed(1)} min`}
        />
        <Stat
          label="Charger utilisation (daily)"
          value={formatPercent(sim.utilisation)}
          detail={`target band ${formatPercent(band[0], 0)}–${formatPercent(band[1], 0)}`}
        />
        <Stat
          label="Peak hour"
          value={`${String(s.peak_hour).padStart(2, '0')}:00`}
          detail={`${s.erlang.peak_arrivals_per_hour.toFixed(1)} arrivals · ${formatPercent(s.erlang.peak_utilisation)} occupied`}
        />
      </div>
      {(s.flags.oversized ||
        s.flags.busy ||
        s.flags.misses_wait_target_in_simulation ||
        s.erlang.capped) && (
        <ul className="list-disc rounded border border-warn p-2 pl-6 text-xs text-warn">
          {s.flags.oversized && (
            <li>Below the utilisation band: one charger is already more than this demand needs.</li>
          )}
          {s.flags.busy && (
            <li>Above the utilisation band: queues are likely outside the peak hour too.</li>
          )}
          {s.flags.misses_wait_target_in_simulation && (
            <li>
              The simulation misses the wait target that Erlang-C met (queues carry over between
              hours).
            </li>
          )}
          {s.erlang.capped && <li>Hit the charger limit without meeting the targets.</li>}
        </ul>
      )}
      <HourlyChart
        title="Arrivals per hour (peak hour highlighted)"
        values={s.hourly_arrivals}
        format={(v) => v.toFixed(1)}
        peak={s.peak_hour}
        c={c}
      />
      <HourlyChart
        title={`Simulated charger occupancy by hour (${formatCount(sim.days)} days)`}
        values={sim.hourly_utilisation}
        format={(v) => `${Math.round(v * 100)}%`}
        peak={s.peak_hour}
        c={c}
      />
      <section>
        <h4 className={h4}>Who charges here</h4>
        <table className="mt-1 w-full text-xs">
          <thead>
            <tr className="text-left text-muted">
              <th className="py-0.5">Segment</th>
              <th className="py-0.5 text-right">Sessions</th>
              <th className="py-0.5 text-right">kWh</th>
              <th className="py-0.5 text-right">kW</th>
              <th className="py-0.5 text-right">Minutes</th>
            </tr>
          </thead>
          <tbody>
            {s.segments.map((g) => (
              <tr key={g.segment} className="border-t border-line">
                <td className="py-0.5">{SEGMENT_LABELS[g.segment] ?? g.segment}</td>
                <td className="py-0.5 text-right tabular-nums">
                  {formatPercent(g.session_share, 0)}
                </td>
                <td className="py-0.5 text-right tabular-nums">{g.kwh_per_session}</td>
                <td className="py-0.5 text-right tabular-nums">{g.effective_kw}</td>
                <td className="py-0.5 text-right tabular-nums">{g.service_minutes}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="mt-1 text-[11px] text-muted">
          kW = what the vehicle actually draws: the charger’s rating after taper, capped by the
          vehicle’s own limit.
        </p>
      </section>
      <section>
        <h4 className={h4}>Connectors</h4>
        <p className="mt-1 text-xs">
          {Object.entries(s.connectors.guns)
            .map(([std, n]) => `${n}× ${std.replace('_', ' ')}`)
            .join(' · ')}{' '}
          <span className="text-muted">
            ({s.connectors.guns_per_charger} guns per charger, split by the connectors sessions
            need)
          </span>
        </p>
      </section>
      <section>
        <h4 className={h4}>Electrical</h4>
        <p className="mt-1 text-xs">
          Sanctioned load {formatCount(s.electrical.sanctioned_kw)} kW ({s.total_kw} kW ×{' '}
          {s.electrical.diversity_factor} diversity) → <b>{s.electrical.connection}</b> connection
          {s.electrical.transformer_required ? ' with its own 11 kV transformer' : ''} (LT limit{' '}
          {s.electrical.lt_max_sanctioned_kw} kW) · {formatLakh(s.electrical.connection_cost_inr)}
        </p>
      </section>
    </div>
  )
}

function q(v: Quantiles, fmt: (n: number) => string, never = 'never'): string {
  const f = (n: number | null) => (n === null ? never : fmt(n))
  return `${f(v.p10)} · ${f(v.p50)} · ${f(v.p90)}`
}

const CAPEX_LABELS: Record<string, string> = {
  chargers: 'Chargers',
  installation: 'Installation',
  civil: 'Civil works',
  canopy: 'Canopy',
  software_networking: 'Software & networking',
  connection: 'Grid connection',
  transformer: 'Transformer',
  contingency: 'Contingency',
  land_lease_deposit: 'Land lease deposit',
  subsidy_upstream: 'Subsidy (upstream grid costs)',
}

/**
 * Tornado as plain HTML bars on one shared scale: ECharts stacks negative and positive
 * values separately, which breaks bars that straddle a negative base NPV.
 */
function Tornado({
  rows,
  base,
  inputs,
  c,
}: {
  rows: SiteEconomics['finance']['tornado']
  base: number
  inputs: SiteEconomics['finance']['monte_carlo']['inputs']
  c: ChartPalette
}) {
  const values = rows.flatMap((r) => [r.npv_low_input, r.npv_high_input]).concat(base)
  const lo = Math.min(...values)
  const hi = Math.max(...values)
  const pct = (v: number) => (hi === lo ? 50 : ((v - lo) / (hi - lo)) * 100)
  return (
    <figure aria-label="Tornado">
      <figcaption className={h4}>What moves NPV most</figcaption>
      <p className="text-[11px] text-muted">
        Each input at its low and high end, others at base. Line = base NPV {formatCrore(base)}.{' '}
        <span style={{ color: c.warm }}>■</span> lowers NPV ·{' '}
        <span style={{ color: c.cool }}>■</span> raises it
      </p>
      <ul className="mt-2 flex flex-col gap-2">
        {rows.map((r) => {
          const a = Math.min(r.npv_low_input, r.npv_high_input)
          const b = Math.max(r.npv_low_input, r.npv_high_input)
          const d = inputs[r.input]
          return (
            <li
              key={r.input}
              className="grid grid-cols-[92px_1fr] items-center gap-2 text-[11px]"
              title={`${r.label}: ×${d.low} → ${formatCrore(r.npv_low_input)}; ×${d.high} → ${formatCrore(r.npv_high_input)}`}
            >
              <span className="truncate">{r.label}</span>
              <div>
                <div className="relative h-3.5">
                  {a < base && (
                    <div
                      className="absolute top-0 h-full rounded-l"
                      style={{
                        left: `${pct(a)}%`,
                        width: `${pct(Math.min(b, base)) - pct(a)}%`,
                        background: c.warm,
                      }}
                    />
                  )}
                  {b > base && (
                    <div
                      className="absolute top-0 h-full rounded-r"
                      style={{
                        left: `${pct(Math.max(a, base))}%`,
                        width: `${pct(b) - pct(Math.max(a, base))}%`,
                        background: c.cool,
                      }}
                    />
                  )}
                  <div
                    className="absolute -top-0.5 h-[18px] w-px"
                    style={{ left: `${pct(base)}%`, background: c.ink }}
                  />
                </div>
                <div className="flex justify-between text-muted tabular-nums">
                  <span style={{ marginLeft: `${Math.min(pct(a), 80)}%` }}>{formatCrore(a)}</span>
                  <span>{formatCrore(b)}</span>
                </div>
              </div>
            </li>
          )
        })}
      </ul>
    </figure>
  )
}

/** Finance tab (Section 9.10): P10/P50/P90, cases, tornado, CAPEX and cash flows. */
function FinanceView({ data }: { data: SiteEconomics }) {
  const c = useChartColors()
  const f = data.finance
  const mc = f.monte_carlo
  const base = f.base.npv
  const cr = (n: number) => formatCrore(n)
  const years = f.base.years
  let cum = 0
  const cumulative = f.base.net.map((v) => (cum += v))
  return (
    <div className="flex flex-col gap-4 text-sm">
      <div className="grid grid-cols-2 gap-2">
        <Stat
          label="NPV · P10 · P50 · P90"
          value={q(mc.npv, cr)}
          detail={`${formatPercent(f.discount_rate, 0)} discount, ${f.horizon_years} years`}
        />
        <Stat label="IRR · P10 · P50 · P90" value={q(mc.irr, (n) => formatPercent(n, 0), 'none')} />
        <Stat
          label="Payback (years) · P10 · P50 · P90"
          value={q(mc.payback_years, (n) => n.toFixed(1), '>10')}
        />
        <Stat
          label="Chance NPV > 0"
          value={formatPercent(mc.prob_npv_positive.p, 0)}
          detail={`${formatCount(mc.draws)} Monte Carlo draws`}
        />
        <Stat
          label="Monthly revenue, steady state (P50)"
          value={formatLakh(mc.monthly_revenue_steady.p50)}
          detail={`at ₹${f.selling_price_inr_per_kwh}/kWh`}
        />
        <Stat
          label="CAPEX"
          value={formatCrore(f.capex_total)}
          detail={`${f.connection} connection`}
        />
      </div>

      <section aria-label="Cases">
        <h4 className={h4}>Cases</h4>
        <table className="mt-1 w-full text-xs">
          <thead>
            <tr className="text-left text-muted">
              <th className="py-0.5">Case</th>
              <th className="py-0.5 text-right">NPV</th>
              <th className="py-0.5 text-right">IRR</th>
              <th className="py-0.5 text-right">Payback</th>
              <th className="py-0.5 text-right">CAPEX</th>
            </tr>
          </thead>
          <tbody>
            {(['pessimistic', 'base', 'optimistic'] as const).map((name) => {
              const k = f.cases[name]
              return (
                <tr key={name} className="border-t border-line">
                  <td className="py-0.5 capitalize">{name}</td>
                  <td className="py-0.5 text-right tabular-nums">{formatCrore(k.npv)}</td>
                  <td className="py-0.5 text-right tabular-nums">
                    {k.irr === null ? 'none' : formatPercent(k.irr, 0)}
                  </td>
                  <td className="py-0.5 text-right tabular-nums">
                    {k.payback_years === null ? '> 10 y' : `${k.payback_years.toFixed(1)} y`}
                  </td>
                  <td className="py-0.5 text-right tabular-nums">{formatCrore(k.capex)}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
        <p className="mt-1 text-[11px] text-muted">
          Pessimistic puts every uncertain input at its adverse end at once; the P10/P50/P90 above
          come from drawing them independently.
        </p>
      </section>

      <Tornado rows={f.tornado} base={base} inputs={mc.inputs} c={c} />

      <figure aria-label="Cash flows">
        <figcaption className={h4}>Base case cash flows</figcaption>
        <p className="text-[11px] text-muted">
          <span style={{ color: c.cool }}>■</span> net cash per year ·{' '}
          <span style={{ color: c.warm }}>—</span> cumulative (payback where it crosses zero)
        </p>
        <ReactECharts
          style={{ height: 170 }}
          option={{
            animation: false,
            grid: { left: 44, right: 12, top: 10, bottom: 24 },
            tooltip: {
              trigger: 'axis',
              valueFormatter: (v: number) => formatCrore(v),
            },
            xAxis: { type: 'category', data: years.map((y) => `Y${y}`), ...axis(c) },
            yAxis: {
              type: 'value',
              ...axis(c),
              axisLabel: { ...axis(c).axisLabel, formatter: (v: number) => (v / 1e7).toFixed(1) },
            },
            series: [
              {
                name: 'Net cash',
                type: 'bar',
                barWidth: '55%',
                itemStyle: { color: c.cool, borderRadius: 4 },
                data: f.base.net,
              },
              {
                name: 'Cumulative',
                type: 'line',
                symbolSize: 8,
                lineStyle: { width: 2, color: c.warm },
                itemStyle: { color: c.warm, borderColor: c.surface, borderWidth: 2 },
                data: cumulative,
              },
            ],
          }}
        />
      </figure>

      <section>
        <h4 className={h4}>CAPEX · {formatCrore(f.capex_total)}</h4>
        <ul className="mt-1 grid grid-cols-2 gap-x-4 text-xs">
          {Object.entries(f.capex_lines)
            .filter(([, v]) => v !== 0)
            .map(([k, v]) => (
              <li key={k} className="flex justify-between">
                <span>{CAPEX_LABELS[k] ?? k}</span>
                <span className="tabular-nums">{formatLakh(v)}</span>
              </li>
            ))}
        </ul>
      </section>
      <p className="text-[11px] text-muted">
        Pre-tax project cash flows, no debt. Selling price ₹{f.selling_price_inr_per_kwh}/kWh, grid
        energy ₹{f.grid_energy_inr_per_kwh}/kWh; time-of-day tariffs and solar/storage arrive in
        Phase 8.
      </p>
    </div>
  )
}

/** Sizing and Finance tabs for a site in a plan (acceptance: Phase 7). */
export function EconomicsTabs({
  optimisationId,
  siteId,
  tab,
}: {
  optimisationId: string
  siteId: string
  tab: 'sizing' | 'finance'
}) {
  const [subsidy, setSubsidy] = useState(false)
  const { data, isError, error, isFetching } = useQuery({
    queryKey: ['economics', optimisationId, siteId, subsidy],
    queryFn: () =>
      getJson<SiteEconomics>(
        `/optimisations/${optimisationId}/sites/${siteId}/economics?subsidy=${subsidy}`,
      ),
    staleTime: Infinity,
  })
  return (
    <div className="flex flex-col gap-3">
      {tab === 'finance' && (
        <label className="flex items-center gap-2 text-xs">
          <input type="checkbox" checked={subsidy} onChange={(e) => setSubsidy(e.target.checked)} />
          Apply a PM E-DRIVE-style subsidy on grid connection costs (eligibility is not assumed)
        </label>
      )}
      {isError && <p className="text-sm text-warn">{(error as Error).message}</p>}
      {!data && !isError && (
        <p className="text-sm text-muted">
          {isFetching ? 'Sizing and simulating 1,000 days… (a few seconds)' : 'Loading…'}
        </p>
      )}
      {data && (
        <>
          <p className="rounded border border-dashed border-synthetic p-2 text-[11px] text-synthetic">
            Illustrative: demand is synthetic and {data.placeholders.length} costs, tariffs and
            sizing parameters are placeholders. This shows the method, not an investment case.
          </p>
          {tab === 'sizing' ? <SizingView data={data} /> : <FinanceView data={data} />}
        </>
      )}
    </div>
  )
}

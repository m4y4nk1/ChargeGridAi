import { useQuery } from '@tanstack/react-query'
import ReactECharts from 'echarts-for-react'
import { Link } from '@tanstack/react-router'
import { useState } from 'react'
import { useChartColors } from '../../components/charts/chartColors'
import { getJson, type EnergyScenario, type SiteEnergy } from '../../lib/api'
import { formatCount, formatCrore, formatLakh, formatPercent } from '../../lib/format'

const h4 = 'text-xs font-semibold uppercase tracking-wide text-muted'
const MONTH_NAMES: Record<string, string> = { '4': 'April (dry, sunny)', '7': 'July (monsoon)' }
const HOURS = Array.from({ length: 24 }, (_, h) => String(h).padStart(2, '0'))

function signed(inr: number, fmt: (n: number) => string): string {
  return inr > 0 ? `+${fmt(inr)}` : fmt(inr)
}

function Stat({ label, value, detail }: { label: string; value: string; detail?: string }) {
  return (
    <div className="rounded border border-line p-2">
      <div className="text-[11px] text-muted">{label}</div>
      <div className="text-base font-semibold tabular-nums">{value}</div>
      {detail && <div className="text-[11px] text-muted">{detail}</div>}
    </div>
  )
}

/** Hourly supply mix for one representative day, against the charging load. */
function DispatchChart({ s, month, load }: { s: EnergyScenario; month: string; load: number[] }) {
  const c = useChartColors()
  const d = s.dispatch[month]
  if (!d) return null
  const axis = {
    axisLine: { lineStyle: { color: c.grid } },
    axisTick: { show: false },
    axisLabel: { color: c.muted, fontSize: 10 },
    splitLine: { lineStyle: { color: c.grid } },
  }
  const bar = (name: string, data: number[], color: string) => ({
    name,
    type: 'bar',
    stack: 'supply',
    barCategoryGap: '15%',
    // 2px surface gap between stacked segments.
    itemStyle: { color, borderColor: c.surface, borderWidth: 1 },
    data: data.map((v) => Math.round(v * 10) / 10),
  })
  return (
    <figure aria-label={`Dispatch ${MONTH_NAMES[month]}`}>
      <figcaption className="text-xs text-muted">
        {MONTH_NAMES[month]}: an average day, kW
      </figcaption>
      <ReactECharts
        style={{ height: 170 }}
        option={{
          animation: false,
          grid: { left: 40, right: 8, top: 26, bottom: 20 },
          legend: {
            top: 0,
            left: 0,
            itemWidth: 10,
            itemHeight: 10,
            textStyle: { color: c.ink, fontSize: 10 },
            data: ['Solar', 'Battery', 'Grid', 'Charging load'],
          },
          tooltip: { trigger: 'axis', valueFormatter: (v: number) => `${v} kW` },
          xAxis: {
            type: 'category',
            data: HOURS,
            ...axis,
            axisLabel: { ...axis.axisLabel, interval: 5 },
          },
          yAxis: {
            type: 'value',
            ...axis,
          },
          series: [
            bar('Solar', d.pv, c.solar),
            bar('Battery', d.dis, c.battery),
            bar('Grid', d.grid, c.mains),
            {
              name: 'Charging load',
              type: 'line',
              step: 'middle',
              symbol: 'none',
              lineStyle: { color: c.ink, width: 2 },
              itemStyle: { color: c.ink },
              data: load.map((v) => Math.round(v * 10) / 10),
            },
          ],
        }}
      />
    </figure>
  )
}

/** Energy tab (Section 9.9): solar + battery recommendation, effect on connection and OPEX. */
export function EnergyTab({
  optimisationId,
  siteId,
  runId,
}: {
  optimisationId: string
  siteId: string
  runId?: string
}) {
  const [month, setMonth] = useState('4')
  const { data, isError, error } = useQuery({
    queryKey: ['energy', optimisationId, siteId],
    queryFn: () => getJson<SiteEnergy>(`/optimisations/${optimisationId}/sites/${siteId}/energy`),
    staleTime: Infinity,
  })
  if (isError) return <p className="text-sm text-warn">{(error as Error).message}</p>
  if (!data) return <p className="text-sm text-muted">Optimising solar and storage…</p>

  const o = data.open
  const rec = o.scenarios[o.recommended]
  const base = o.scenarios.grid_only
  const e = o.effect
  const order = ['grid_only', 'pv', 'pv_battery', 'pv_battery_lt'].filter((k) => o.scenarios[k])
  return (
    <div className="flex flex-col gap-4 text-sm">
      <p className="rounded border border-dashed border-synthetic p-2 text-[11px] text-synthetic">
        Illustrative: demand is synthetic and {data.placeholders.length} prices, tariffs and
        equipment parameters are placeholders.
      </p>
      <div>
        <p className="text-base font-semibold">Recommended: {rec.label}</p>
        <p className="text-xs text-muted">
          {rec.pv_kwp > 0 ? `${formatCount(rec.pv_kwp)} kWp solar` : 'No solar'}
          {rec.battery_kwh > 0
            ? ` · ${formatCount(Math.round(rec.battery_kwh))} kWh / ${formatCount(Math.round(rec.battery_kw))} kW battery`
            : ' · no battery'}{' '}
          · {rec.connection} connection, {formatCount(Math.round(rec.contract_kw))} kW contract
        </p>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <Stat
          label="Grid connection"
          value={
            e.connection_before === e.connection_after
              ? `${e.connection_after}, unchanged type`
              : `${e.connection_before} → ${e.connection_after}`
          }
          detail={`contract ${formatCount(Math.round(base.contract_kw))} → ${formatCount(Math.round(rec.contract_kw))} kW`}
        />
        <Stat
          label="Energy + demand charges (OPEX)"
          value={`${signed(e.annual_opex_change, formatLakh)}/yr`}
          detail={`${formatLakh(base.annual.opex)} → ${formatLakh(rec.annual.opex)}`}
        />
        <Stat
          label="Added CAPEX"
          value={formatCrore(e.added_capex)}
          detail={
            e.simple_payback_years === null
              ? 'no OPEX saving to pay it back'
              : `simple payback ${e.simple_payback_years} years`
          }
        />
        <Stat
          label="Total annual cost"
          value={`${signed(e.annual_total_change, formatLakh)}/yr`}
          detail="incl. annualised solar, battery and connection"
        />
      </div>

      <section aria-label="Energy scenarios">
        <h4 className={h4}>Options compared (per year)</h4>
        <table className="mt-1 w-full text-xs">
          <thead>
            <tr className="text-left text-muted">
              <th className="py-0.5">Option</th>
              <th className="py-0.5 text-right">Contract</th>
              <th className="py-0.5 text-right">OPEX</th>
              <th className="py-0.5 text-right">Total</th>
              <th className="py-0.5 text-right">Solar</th>
            </tr>
          </thead>
          <tbody>
            {order.map((k) => {
              const s = o.scenarios[k]
              return (
                <tr
                  key={k}
                  className={`border-t border-line ${k === o.recommended ? 'font-semibold' : ''}`}
                >
                  <td className="py-0.5">
                    {s.label}
                    {k === o.recommended && ' ✓'}
                  </td>
                  {s.status === 'optimal' ? (
                    <>
                      <td className="py-0.5 text-right tabular-nums">
                        {s.connection} {formatCount(Math.round(s.contract_kw))} kW
                      </td>
                      <td className="py-0.5 text-right tabular-nums">
                        {formatLakh(s.annual.opex)}
                      </td>
                      <td className="py-0.5 text-right tabular-nums">
                        {formatLakh(s.annual.total)}
                      </td>
                      <td className="py-0.5 text-right tabular-nums">
                        {formatPercent(s.solar_share, 0)}
                      </td>
                    </>
                  ) : (
                    <td colSpan={4} className="py-0.5 text-right text-muted">
                      not feasible
                    </td>
                  )}
                </tr>
              )
            })}
          </tbody>
        </table>
        <p className="mt-1 text-[11px] text-muted">
          Total = energy + demand charges + solar, battery and grid connection spread over their
          lives at the finance discount rate. Contract + battery power always covers the chargers’{' '}
          {formatCount(data.load.design_peak_kw)} kW short peak.
        </p>
      </section>

      <section aria-label="Dispatch">
        <div className="flex items-center justify-between">
          <h4 className={h4}>How the recommended option runs</h4>
          <div role="radiogroup" aria-label="Month" className="flex gap-1 text-xs">
            {Object.keys(MONTH_NAMES).map((m) => (
              <button
                key={m}
                type="button"
                role="radio"
                aria-checked={month === m}
                onClick={() => setMonth(m)}
                className={`rounded px-2 py-0.5 ${month === m ? 'bg-ink text-paper' : 'border border-line'}`}
              >
                {m === '4' ? 'Apr' : 'Jul'}
              </button>
            ))}
          </div>
        </div>
        <DispatchChart s={rec} month={month} load={data.load.hourly_kw} />
        <p className="text-[11px] text-muted">
          Bars above the load line charge the battery; the battery discharges into the evening peak
          and the time-of-day peak price.
        </p>
      </section>

      <section>
        <h4 className={h4}>Solar resource</h4>
        <p className="mt-1 text-xs">
          {formatCount(data.solar.annual_ghi_kwh_m2)} kWh/m² a year, about{' '}
          {formatCount(data.solar.specific_yield_kwh_per_kwp)} kWh per kWp. PV limited to{' '}
          {formatCount(o.pv_limit.kwp)} kWp ({o.pv_limit.source}); roofs of host buildings are not
          mapped yet. <span className="text-muted">Source: {data.solar.attribution}.</span>
        </p>
        <p className="mt-1 text-[11px] text-muted">
          {data.google_available && runId ? (
            <Link
              to="/runs/$runId/sites/$siteId/google"
              params={{ runId, siteId }}
              search={{ opt: optimisationId }}
              className="underline"
            >
              Google Solar has this roof: open the site in Google map mode
            </Link>
          ) : (
            data.google_note
          )}
        </p>
      </section>
    </div>
  )
}

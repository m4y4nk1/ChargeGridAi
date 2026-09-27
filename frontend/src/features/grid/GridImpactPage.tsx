/**
 * Grid Impact (module E, Section 12): the plan's charger profile, its 24-hour load stacked
 * by vehicle segment or charger class, and the grid assets it lands on. Without DISCOM
 * loading data this is a proximity-only estimate and says so above everything else.
 */
import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import ReactECharts from 'echarts-for-react'
import { useState } from 'react'
import { ConfidenceBadge } from '../../components/badges'
import { useChartColors } from '../../components/charts/chartColors'
import { getJson } from '../../lib/api'
import { formatCount, formatCrore, formatPercent } from '../../lib/format'
import { type GridImpact, SEGMENT_LABELS, UTILISATION_BINS, binUtilisation } from '../../lib/grid'

const HOURS = Array.from({ length: 24 }, (_, h) => `${String(h).padStart(2, '0')}`)
const CLASS_ORDER = ['AC_7', 'AC_22', 'DC_30', 'DC_60', 'DC_120', 'DC_180', 'DC_240', 'DC_350']
const SEGMENT_ORDER = Object.keys(SEGMENT_LABELS)

const card = 'rounded border border-line bg-panel px-3 py-2'
const h2 = 'text-sm font-semibold'

/** Series in a fixed entity order so colour follows the entity, never its rank. */
function ordered(series: Record<string, number[]>, order: string[]): [string, number[]][] {
  const rank = (k: string) => (order.includes(k) ? order.indexOf(k) : order.length)
  return Object.entries(series).sort((a, b) => rank(a[0]) - rank(b[0]) || a[0].localeCompare(b[0]))
}

function Stat({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className={card}>
      <p className="text-xs text-muted">{label}</p>
      <p className="text-lg font-semibold tabular-nums">{value}</p>
      {note && <p className="text-xs text-muted">{note}</p>}
    </div>
  )
}

function LoadCurve({ data }: { data: GridImpact }) {
  const c = useChartColors()
  const [by, setBy] = useState<'segment' | 'class'>('segment')
  const [table, setTable] = useState(false)
  const series =
    by === 'segment'
      ? ordered(data.load_curve.by_segment, SEGMENT_ORDER)
      : ordered(data.load_curve.by_class, CLASS_ORDER)
  const label = (k: string) =>
    by === 'segment' ? (SEGMENT_LABELS[k] ?? k) : k.replace('_', ' ') + ' kW'
  const axis = {
    axisLine: { lineStyle: { color: c.grid } },
    axisTick: { show: false },
    axisLabel: { color: c.muted, fontSize: 10 },
    splitLine: { lineStyle: { color: c.grid } },
  }
  return (
    <section aria-label="Plan load curve" className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className={h2}>Charging load over a day, kW</h2>
        <div role="tablist" aria-label="Stack by" className="ml-auto flex gap-1 text-xs">
          {(['segment', 'class'] as const).map((k) => (
            <button
              key={k}
              type="button"
              role="tab"
              aria-selected={by === k}
              onClick={() => setBy(k)}
              className={`rounded px-2 py-0.5 ${by === k ? 'bg-ink text-paper' : 'border border-line'}`}
            >
              {k === 'segment' ? 'By vehicle segment' : 'By charger class'}
            </button>
          ))}
          <button
            type="button"
            onClick={() => setTable((t) => !t)}
            className="rounded border border-line px-2 py-0.5"
          >
            {table ? 'Chart' : 'Table'}
          </button>
        </div>
      </div>
      {table ? (
        <div className="overflow-x-auto">
          <table className="w-full text-xs tabular-nums">
            <thead>
              <tr className="text-left text-muted">
                <th className="px-1">Hour</th>
                {series.map(([k]) => (
                  <th key={k} className="px-1">
                    {label(k)}
                  </th>
                ))}
                <th className="px-1">Total</th>
              </tr>
            </thead>
            <tbody>
              {HOURS.map((h, i) => (
                <tr key={h} className="border-t border-line">
                  <td className="px-1">{h}:00</td>
                  {series.map(([k, v]) => (
                    <td key={k} className="px-1">
                      {formatCount(v[i])}
                    </td>
                  ))}
                  <td className="px-1 font-medium">{formatCount(data.load_curve.total[i])}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <ReactECharts
          style={{ height: 260 }}
          option={{
            animation: false,
            grid: { left: 48, right: 8, top: 30, bottom: 24 },
            legend: {
              top: 0,
              left: 0,
              itemWidth: 10,
              itemHeight: 10,
              textStyle: { color: c.ink, fontSize: 11 },
            },
            tooltip: {
              trigger: 'axis',
              axisPointer: { type: 'shadow' },
              valueFormatter: (v: number) => `${formatCount(v)} kW`,
            },
            xAxis: {
              type: 'category',
              data: HOURS,
              ...axis,
              axisLabel: { ...axis.axisLabel, interval: 2 },
            },
            yAxis: { type: 'value', ...axis },
            series: series.map(([k, v], i) => ({
              name: label(k),
              type: 'bar',
              stack: 'load',
              barCategoryGap: '20%',
              itemStyle: {
                color: c.series[i % c.series.length],
                borderColor: c.surface,
                borderWidth: 1,
                borderRadius: i === series.length - 1 ? [4, 4, 0, 0] : 0,
              },
              data: v,
            })),
          }}
        />
      )}
      <p className="text-xs text-muted">
        Peak {formatCount(data.load_curve.peak_kw)} kW at {data.load_curve.peak_hour}:00 (hourly
        average), against {formatCount(data.load_curve.sanctioned_kw_total)} kW of sanctioned
        connections.
      </p>
    </section>
  )
}

function Utilisation({ data }: { data: GridImpact }) {
  const c = useChartColors()
  if (!data.utilisation) return null
  const base = binUtilisation(data.utilisation.base)
  const withCharging = binUtilisation(data.utilisation.with_charging)
  return (
    <section aria-label="Asset utilisation" className="flex flex-col gap-2">
      <h2 className={h2}>Grid assets by peak utilisation (share of rating)</h2>
      <ReactECharts
        style={{ height: 200 }}
        option={{
          animation: false,
          grid: { left: 36, right: 8, top: 30, bottom: 24 },
          legend: {
            top: 0,
            left: 0,
            itemWidth: 10,
            itemHeight: 10,
            textStyle: { color: c.ink, fontSize: 11 },
          },
          tooltip: { trigger: 'axis', valueFormatter: (v: number) => `${v} assets` },
          xAxis: {
            type: 'category',
            data: UTILISATION_BINS,
            axisLabel: { color: c.muted, fontSize: 10 },
          },
          yAxis: {
            type: 'value',
            minInterval: 1,
            axisLabel: { color: c.muted, fontSize: 10 },
            splitLine: { lineStyle: { color: c.grid } },
          },
          series: [
            {
              name: 'Today',
              type: 'bar',
              data: base,
              itemStyle: { color: c.cool, borderRadius: [4, 4, 0, 0] },
            },
            {
              name: 'With the plan',
              type: 'bar',
              data: withCharging,
              itemStyle: { color: c.warm, borderRadius: [4, 4, 0, 0] },
            },
          ],
        }}
      />
    </section>
  )
}

export function GridImpactPage({ runId }: { runId: string }) {
  const q = useQuery({
    queryKey: ['grid-impact', runId],
    queryFn: () => getJson<GridImpact>(`/grid/impact?run_id=${runId}`),
  })
  const d = q.data
  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto flex max-w-5xl flex-col gap-5 p-6">
        <div>
          <Link to="/runs/$runId" params={{ runId }} className="text-xs underline">
            ← Back to the plan
          </Link>
          <h1 className="mt-1 text-lg font-semibold">Grid impact</h1>
        </div>
        {q.isLoading && <p className="text-sm text-muted">Computing load curves…</p>}
        {q.error && (
          <p role="alert" className="text-sm text-warn">
            {q.error.message}
          </p>
        )}
        {d && (
          <>
            <div
              role="note"
              className={`flex flex-wrap items-center gap-2 rounded px-3 py-2 text-sm ${
                d.mode === 'discom' ? 'border border-line' : 'border border-warn bg-warn/10'
              }`}
            >
              <strong className={d.mode === 'discom' ? '' : 'text-warn'}>{d.badge}.</strong>
              <ConfidenceBadge value={d.confidence} />
              {d.mode !== 'discom' && (
                <span className="text-muted">
                  Utilisation and headroom need DISCOM transformer/substation loadings (make
                  ingest-discom).
                </span>
              )}
            </div>
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
              <Stat
                label="Plan peak load"
                value={`${formatCount(d.load_curve.peak_kw)} kW`}
                note={`at ${d.load_curve.peak_hour}:00`}
              />
              <Stat label="Energy per day" value={`${formatCount(d.load_curve.daily_kwh)} kWh`} />
              <Stat
                label="Connections"
                value={`${d.connections.ht_sites} HT · ${d.connections.lt_sites} LT`}
                note={`${d.connections.transformers_required} site transformers`}
              />
              <Stat
                label="Connection cost"
                value={formatCrore(d.connections.connection_cost_inr)}
                note="illustrative unit costs"
              />
            </div>
            <LoadCurve data={d} />
            <section aria-label="Charger profile" className="grid gap-4 md:grid-cols-2">
              <div>
                <h2 className={h2}>Chargers by class</h2>
                <table className="mt-1 w-full text-sm tabular-nums">
                  <thead>
                    <tr className="text-left text-xs text-muted">
                      <th>Class</th>
                      <th>Sites</th>
                      <th>Chargers</th>
                      <th>kW</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(d.charger_profile.by_class).map(([k, v]) => (
                      <tr key={k} className="border-t border-line">
                        <td>{k.replace('_', ' ')} kW</td>
                        <td>{v.sites}</td>
                        <td>{v.chargers}</td>
                        <td>{formatCount(v.kw)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div>
                <h2 className={h2}>Sites by host type</h2>
                <ul className="mt-1 text-sm">
                  {Object.entries(d.charger_profile.by_host_type)
                    .sort((a, b) => b[1] - a[1])
                    .map(([k, n]) => (
                      <li key={k} className="flex justify-between border-t border-line py-0.5">
                        <span>{k.replaceAll('_', ' ').toLowerCase()}</span>
                        <span className="tabular-nums">{n}</span>
                      </li>
                    ))}
                </ul>
              </div>
            </section>
            {d.assets.length > 0 ? (
              <>
                {d.upgrades && (
                  <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
                    <Stat
                      label="Assets overloaded by the plan"
                      value={String(d.upgrades.assets_overloaded)}
                    />
                    <Stat
                      label="Transformer replacements"
                      value={String(d.upgrades.transformers_to_replace)}
                      note={`${formatCrore(d.upgrades.transformer_cost_inr)} (illustrative unit costs)`}
                    />
                    <Stat
                      label="Substations needing a DISCOM study"
                      value={String(d.upgrades.substations_needing_study)}
                      note="not costed"
                    />
                  </div>
                )}
                <Utilisation data={d} />
                <section aria-label="Grid assets">
                  <h2 className={h2}>Assets serving the plan</h2>
                  <table className="mt-1 w-full text-sm tabular-nums">
                    <thead>
                      <tr className="text-left text-xs text-muted">
                        <th>Asset</th>
                        <th>Rating</th>
                        <th>Today</th>
                        <th>With plan</th>
                        <th>Headroom</th>
                        <th>Action</th>
                        <th>Confidence</th>
                      </tr>
                    </thead>
                    <tbody>
                      {d.assets.map((a) => (
                        <tr key={a.asset_id} className="border-t border-line align-top">
                          <td>
                            {a.name ?? a.asset_id}
                            <span className="block text-xs text-muted">
                              {a.asset_type} · {a.site_ids.length} site
                              {a.site_ids.length === 1 ? '' : 's'}
                            </span>
                          </td>
                          <td>{formatCount(a.rated_kva)} kVA</td>
                          <td>{formatPercent(a.utilisation_base, 0)}</td>
                          <td className={a.overloaded ? 'font-semibold text-warn' : ''}>
                            {formatPercent(a.utilisation_with, 0)}
                            {a.overloaded && ' ⚠ over limit'}
                          </td>
                          <td>{formatCount(a.headroom_kva)} kVA</td>
                          <td className="text-xs">
                            {a.upgrade ?? '—'}
                            {a.upgrade_cost_inr !== null && ` (${formatCrore(a.upgrade_cost_inr)})`}
                          </td>
                          <td>
                            <ConfidenceBadge value={a.confidence} />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </section>
              </>
            ) : (
              <section aria-label="Nearest substations">
                <h2 className={h2}>Load added at the nearest mapped substations</h2>
                <p className="text-xs text-muted">
                  Proximity only: which OSM substation each site is closest to and the charging load
                  it would add. Their ratings and current loading are unknown, so no utilisation or
                  headroom is shown.
                </p>
                <table className="mt-1 w-full text-sm tabular-nums">
                  <thead>
                    <tr className="text-left text-xs text-muted">
                      <th>Substation (OSM)</th>
                      <th>Sites</th>
                      <th>Added peak</th>
                      <th>Sanctioned load</th>
                    </tr>
                  </thead>
                  <tbody>
                    {d.proxy_substations.map((p) => (
                      <tr key={p.osm_id} className="border-t border-line">
                        <td>
                          {p.osm_id}
                          {p.voltage_kv ? ` · ${p.voltage_kv} kV` : ''}
                        </td>
                        <td>{p.site_count}</td>
                        <td>{formatCount(p.added_peak_kw)} kW</td>
                        <td>{formatCount(p.sanctioned_kw)} kW</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </section>
            )}
            {d.placeholders.length > 0 && (
              <p className="text-xs text-muted">
                {d.placeholders.length} placeholder assumptions in use (config/grid.yaml,
                finance.yaml).
              </p>
            )}
          </>
        )}
      </div>
    </div>
  )
}

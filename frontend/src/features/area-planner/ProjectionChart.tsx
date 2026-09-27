import ReactECharts from 'echarts-for-react'
import type { Projection } from '../../lib/api'
import { formatCount } from '../../lib/format'

/** EV stock: observed history, then the forecast P50 with its P10–P90 band. */
export function ProjectionChart({
  data,
  selectedYear,
}: {
  data: Projection
  selectedYear: number
}) {
  const years = [
    ...new Set([...data.observed.map((p) => p.year), ...data.forecast.map((p) => p.year)]),
  ].sort()
  const byYear = <T extends { year: number }>(points: T[]) =>
    new Map(points.map((p) => [p.year, p]))
  const observed = byYear(data.observed)
  const forecast = byYear(data.forecast)

  return (
    <ReactECharts
      style={{ height: 280 }}
      option={{
        animation: false,
        grid: { left: 64, right: 16, top: 28, bottom: 28 },
        legend: {
          top: 0,
          textStyle: { fontSize: 11 },
          data: ['P10–P90 range', 'Forecast (P50)', `Observed (to ${data.observed_through})`],
        },
        tooltip: {
          trigger: 'axis',
          valueFormatter: (v: number) => formatCount(Math.round(v)),
        },
        xAxis: { type: 'category', data: years.map(String), boundaryGap: false },
        yAxis: {
          type: 'value',
          axisLabel: { formatter: (v: number) => formatCount(v) },
          splitLine: { lineStyle: { opacity: 0.3 } },
        },
        series: [
          {
            name: 'P10',
            type: 'line',
            stack: 'band',
            symbol: 'none',
            lineStyle: { opacity: 0 },
            data: years.map((y) => forecast.get(y)?.p10 ?? null),
            tooltip: { show: false },
          },
          {
            name: 'P10–P90 range',
            type: 'line',
            stack: 'band',
            symbol: 'none',
            lineStyle: { opacity: 0 },
            itemStyle: { color: '#9ecae1' },
            areaStyle: { color: '#3690c0', opacity: 0.18 },
            data: years.map((y) => {
              const p = forecast.get(y)
              return p ? p.p90 - p.p10 : null
            }),
          },
          {
            name: 'Forecast (P50)',
            type: 'line',
            symbol: 'circle',
            symbolSize: 4,
            itemStyle: { color: '#0570b0' },
            data: years.map((y) => forecast.get(y)?.p50 ?? null),
            markLine: {
              symbol: 'none',
              silent: true,
              lineStyle: { color: '#d9480f', type: 'dashed' },
              label: { show: false },
              data: [{ xAxis: String(selectedYear) }],
            },
          },
          {
            name: `Observed (to ${data.observed_through})`,
            type: 'line',
            symbol: 'circle',
            symbolSize: 4,
            itemStyle: { color: '#13212b' },
            data: years.map((y) => observed.get(y)?.p50 ?? null),
          },
        ],
      }}
    />
  )
}

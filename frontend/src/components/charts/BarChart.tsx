import ReactECharts from 'echarts-for-react'
import type { CountRow } from '../../lib/api'

export function BarChart({
  title,
  rows,
  metric,
  colors,
}: {
  title: string
  rows: CountRow[]
  metric: 'stations' | 'charge_points' | 'connectors'
  colors?: Record<string, string>
}) {
  const shown = rows.slice(0, 8).reverse()
  return (
    <section aria-label={title}>
      <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted">{title}</h3>
      {shown.length === 0 ? (
        <p className="text-xs text-muted">No data.</p>
      ) : (
        <ReactECharts
          style={{ height: 24 + shown.length * 22 }}
          option={{
            animation: false,
            grid: { left: 110, right: 24, top: 4, bottom: 4 },
            xAxis: { type: 'value', show: false },
            yAxis: {
              type: 'category',
              data: shown.map((r) => r.key),
              axisLabel: { width: 100, overflow: 'truncate', fontSize: 11 },
              axisTick: { show: false },
            },
            series: [
              {
                type: 'bar',
                data: shown.map((r) => ({
                  value: r[metric],
                  itemStyle: { color: colors?.[r.key] ?? '#3690c0' },
                })),
                label: { show: true, position: 'right', fontSize: 11 },
                barWidth: 12,
              },
            ],
            tooltip: { trigger: 'axis', axisPointer: { type: 'none' } },
          }}
        />
      )}
    </section>
  )
}

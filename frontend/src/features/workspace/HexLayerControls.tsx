import { ConfidenceBadge } from '../../components/badges'
import type { H3Layer } from '../../lib/api'
import { formatCount } from '../../lib/format'
import { hexClasses, type HexKind } from '../../map/hexLayer'

export interface HexSelection {
  kind: HexKind | 'none'
  year: number
  scenario: string
}

const YEARS = Array.from({ length: 10 }, (_, i) => 2026 + i)

export function HexLayerControls({
  value,
  onChange,
}: {
  value: HexSelection
  onChange: (v: HexSelection) => void
}) {
  const select = 'mt-1 w-full rounded border border-line bg-panel px-2 py-1 text-sm text-ink'
  return (
    <div className="flex flex-col gap-2 border-b border-line p-4">
      <h2 className="text-sm font-semibold">Demand layers</h2>
      <label className="text-xs text-muted">
        Hexagons show
        <select
          className={select}
          value={value.kind}
          onChange={(e) => onChange({ ...value, kind: e.target.value as HexSelection['kind'] })}
        >
          <option value="none">Nothing</option>
          <option value="demand">Public charging demand</option>
          <option value="gap">Unmet demand (gap)</option>
        </select>
      </label>
      {value.kind !== 'none' && (
        <div className="grid grid-cols-2 gap-2">
          <label className="text-xs text-muted">
            Year
            <select
              className={select}
              value={value.year}
              onChange={(e) => onChange({ ...value, year: Number(e.target.value) })}
            >
              {YEARS.map((y) => (
                <option key={y} value={y}>
                  {y}
                </option>
              ))}
            </select>
          </label>
          <label className="text-xs text-muted">
            Adoption
            <select
              className={select}
              value={value.scenario}
              onChange={(e) => onChange({ ...value, scenario: e.target.value })}
            >
              <option value="slow">Slow</option>
              <option value="base">Base</option>
              <option value="fast">Fast</option>
            </select>
          </label>
        </div>
      )}
    </div>
  )
}

export function HexLegend({ layer, kind }: { layer: H3Layer; kind: HexKind }) {
  const { breaks, colors } = hexClasses(layer.value, kind)
  const labels = colors.slice(0, breaks.length + 1).map((_, i) => {
    const lo = i === 0 ? 0 : breaks[i - 1]
    return i < breaks.length
      ? `${formatCount(Math.round(lo))}–${formatCount(Math.round(breaks[i]))}`
      : `> ${formatCount(Math.round(lo))}`
  })
  return (
    <div className="absolute top-3 left-3 rounded border border-line bg-panel/95 p-2 text-xs shadow">
      <div className="mb-1 font-semibold">
        {kind === 'demand' ? 'Public charging demand' : 'Unmet demand'} · {layer.year} ·{' '}
        {layer.scenario}
      </div>
      <div className="mb-1 text-muted">{layer.unit} per hexagon (~0.74 km²)</div>
      {labels.map((label, i) => (
        <div key={label} className="flex items-center gap-2">
          <span className="inline-block h-2.5 w-4" style={{ background: colors[i] }} />
          {label}
        </div>
      ))}
      <div className="mt-1">
        <ConfidenceBadge value={layer.confidence} />
      </div>
    </div>
  )
}

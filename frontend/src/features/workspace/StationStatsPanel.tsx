import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { BarChart } from '../../components/charts/BarChart'
import { getJson, type RegionSummary, type StationStats } from '../../lib/api'
import { formatCount } from '../../lib/format'
import { POWER_BAND_COLORS, POWER_BAND_ORDER } from '../../map/powerBands'

function Kpi({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded border border-line bg-panel px-3 py-2">
      <div className="text-xl font-semibold tabular-nums">{formatCount(value)}</div>
      <div className="text-xs text-muted">{label}</div>
    </div>
  )
}

export function StationStatsPanel({ onEvidence }: { onEvidence: (ids: string[]) => void }) {
  const [regionId, setRegionId] = useState<number | null>(null)
  const regions = useQuery({
    queryKey: ['regions'],
    queryFn: () => getJson<RegionSummary[]>('/regions'),
  })
  const stats = useQuery({
    queryKey: ['station-stats', regionId],
    queryFn: () =>
      getJson<StationStats>(`/stations/stats${regionId ? `?region_id=${regionId}` : ''}`),
  })
  const byBand = [...(stats.data?.by_power_band ?? [])].sort(
    (a, b) => POWER_BAND_ORDER.indexOf(a.key) - POWER_BAND_ORDER.indexOf(b.key),
  )

  return (
    <div className="flex flex-col gap-4 p-4">
      <div>
        <h2 className="text-sm font-semibold">Existing public charging</h2>
        <label className="mt-2 block text-xs text-muted" htmlFor="region-select">
          Area
        </label>
        <select
          id="region-select"
          className="mt-1 w-full rounded border border-line bg-panel px-2 py-1 text-sm"
          value={regionId ?? ''}
          onChange={(e) => setRegionId(e.target.value ? Number(e.target.value) : null)}
        >
          <option value="">All ingested data</option>
          {regions.data?.map((r) => (
            <option key={r.id} value={r.id}>
              {r.name} ({r.level})
            </option>
          ))}
        </select>
      </div>

      {stats.isError && <p className="text-sm text-warn">Couldn’t load charger statistics.</p>}
      {stats.data && (
        <>
          <div className="grid grid-cols-3 gap-2">
            <Kpi label="Stations" value={stats.data.stations} />
            <Kpi label="Charge points" value={stats.data.charge_points} />
            <Kpi label="Connectors" value={stats.data.connectors} />
          </div>
          <BarChart title="Stations by operator" rows={stats.data.by_operator} metric="stations" />
          <BarChart
            title="Charge points by power band"
            rows={byBand}
            metric="charge_points"
            colors={POWER_BAND_COLORS}
          />
          <BarChart title="Connectors by type" rows={stats.data.by_connector} metric="connectors" />
          <p className="text-xs text-muted">{stats.data.note}</p>
          <button
            type="button"
            className="self-start text-xs underline"
            onClick={() => onEvidence(stats.data.evidence_ids)}
          >
            Sources for these numbers
          </button>
        </>
      )}
    </div>
  )
}

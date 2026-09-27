import { useQuery } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { getJson, type H3Layer } from '../../lib/api'
import { BasemapToggle } from '../../map/BasemapToggle'
import { GoogleLayersMap } from '../../map/GoogleLayersMap'
import { OpenMapCanvas } from '../../map/OpenMapCanvas'
import { useBasemap } from '../../map/useBasemap'
import { POWER_BAND_COLORS } from '../../map/powerBands'
import { EvidenceDrawer, type EvidenceTarget } from '../evidence/EvidenceDrawer'
import { HexLayerControls, HexLegend, type HexSelection } from './HexLayerControls'
import { StationStatsPanel } from './StationStatsPanel'

function Legend() {
  return (
    <div className="absolute bottom-8 left-3 rounded border border-line bg-panel/95 p-2 text-xs shadow">
      <div className="mb-1 font-semibold">Existing chargers · max power</div>
      {Object.entries(POWER_BAND_COLORS).map(([band, color]) => (
        <div key={band} className="flex items-center gap-2">
          <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: color }} />
          {band}
        </div>
      ))}
      <div className="mt-1 text-muted">Fainter = lower confidence</div>
    </div>
  )
}

export function WorkspacePage() {
  const [evidence, setEvidence] = useState<EvidenceTarget>(null)
  const [basemap, setBasemap] = useBasemap()
  const [hexSel, setHexSel] = useState<HexSelection>({ kind: 'none', year: 2030, scenario: 'base' })
  const selectedStationId = evidence?.kind === 'station' ? evidence.stationId : null

  const hexKind = hexSel.kind === 'none' ? null : hexSel.kind
  const hexLayer = useQuery({
    queryKey: ['hex', hexSel],
    queryFn: () =>
      getJson<H3Layer>(
        `/${hexKind === 'gap' ? 'gap' : 'demand'}/h3?year=${hexSel.year}&scenario=${hexSel.scenario}`,
      ),
    enabled: hexKind !== null,
  })
  const hex = useMemo(
    () => (hexKind && hexLayer.data ? { data: hexLayer.data, kind: hexKind } : null),
    [hexKind, hexLayer.data],
  )

  return (
    <div className="grid h-full grid-cols-[320px_1fr] overflow-hidden">
      <div className="overflow-y-auto border-r border-line bg-paper">
        <HexLayerControls value={hexSel} onChange={setHexSel} />
        {hexLayer.isError && (
          <p className="px-4 pt-2 text-xs text-warn">
            No demand run yet — run <code>make demand</code>.
          </p>
        )}
        <StationStatsPanel onEvidence={(ids) => setEvidence({ kind: 'evidence', ids })} />
      </div>
      <div className="relative">
        {basemap === 'google' ? (
          <GoogleLayersMap
            showStations
            onSelectStation={(id) => setEvidence(id ? { kind: 'station', stationId: id } : null)}
            hex={hex}
          />
        ) : (
          <OpenMapCanvas
            selectedStationId={selectedStationId}
            onSelectStation={(id) => setEvidence(id ? { kind: 'station', stationId: id } : null)}
            hex={hex}
          />
        )}
        <BasemapToggle value={basemap} onChange={setBasemap} />
        {hex && <HexLegend layer={hex.data} kind={hex.kind} />}
        <Legend />
        <EvidenceDrawer target={evidence} onClose={() => setEvidence(null)} />
      </div>
    </div>
  )
}

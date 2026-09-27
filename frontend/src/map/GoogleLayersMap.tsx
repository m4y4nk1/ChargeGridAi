/**
 * Google map mode (ADR 0002): Google Maps basemap with our own layers drawn by deck.gl's
 * GoogleMapsOverlay (existing chargers, demand/gap hexagons, candidate and plan sites).
 * Live Google Places around a selected site appear only here: they're fetched on
 * request, never stored, and never shown on the open MapLibre map.
 */
import { GoogleMapsOverlay } from '@deck.gl/google-maps'
import { GeoJsonLayer, ScatterplotLayer } from '@deck.gl/layers'
import { APIProvider, Map, useMap } from '@vis.gl/react-google-maps'
import type { Feature, FeatureCollection, Point } from 'geojson'
import { useEffect, useMemo, useState } from 'react'
import { getJson, type H3Layer, type SiteProperties } from '../lib/api'
import { hexClasses, hexGeoJson, type HexKind } from './hexLayer'
import { SITE_COLORS } from './OpenMapCanvas'
import { POWER_BAND_COLORS } from './powerBands'

const PMR_CENTER = { lat: 18.52, lng: 73.86 }

export interface GooglePlace {
  place_id: string
  name: string
  category: string
  lat: number
  lng: number
}

interface Props {
  hex?: { data: H3Layer; kind: HexKind } | null
  sites?: FeatureCollection<Point, SiteProperties> | null
  selectedSiteId?: string | null
  onSelectSite?: (id: string | null) => void
  onSelectStation?: (id: string | null) => void
  cellFill?: FeatureCollection | null
  showStations?: boolean
}

function rgb(hex: string, alpha = 255): [number, number, number, number] {
  const n = parseInt(hex.replace('#', ''), 16)
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255, alpha]
}

function siteColor(p: SiteProperties & { role?: string }): string {
  if (p.role && p.role in SITE_COLORS) return SITE_COLORS[p.role as keyof typeof SITE_COLORS]
  if (p.selected) return SITE_COLORS.selected
  return SITE_COLORS[(p.status as keyof typeof SITE_COLORS) ?? 'proposed'] ?? SITE_COLORS.proposed
}

function Overlay({
  hex,
  sites,
  selectedSiteId,
  onSelectSite,
  onSelectStation,
  cellFill,
  showStations,
  places,
}: Props & { places: GooglePlace[] }) {
  const map = useMap()
  const [stations, setStations] = useState<FeatureCollection | null>(null)
  // One overlay for the component's life, attached one tick after mount: React's dev
  // strict mode mounts effects twice, and detaching while Google is still adding the
  // overlay leaves it without a map. Overlaid, not interleaved (that needs a Map ID).
  const overlay = useMemo(() => new GoogleMapsOverlay({ interleaved: false }), [])
  useEffect(() => {
    if (!showStations) return
    getJson<FeatureCollection>('/stations')
      .then(setStations)
      .catch(() => setStations(null))
  }, [showStations])
  useEffect(() => {
    if (!map) return
    let attached = false
    const timer = window.setTimeout(() => {
      overlay.setMap(map)
      attached = true
    }, 0)
    return () => {
      window.clearTimeout(timer)
      if (attached) overlay.setMap(null)
    }
  }, [map, overlay])
  // Fly to a site when it's selected, close enough to see its Google places.
  useEffect(() => {
    const f = sites?.features.find((x) => x.properties.id === selectedSiteId)
    if (!map || !f) return
    const [lng, lat] = f.geometry.coordinates
    map.panTo({ lat, lng })
    if ((map.getZoom() ?? 0) < 15) map.setZoom(15)
  }, [map, sites, selectedSiteId])
  const hexData = useMemo(
    () => (hex ? hexGeoJson(hex.data, hexClasses(hex.data.value, hex.kind)) : null),
    [hex],
  )
  useEffect(() => {
    const layers = [
      (cellFill ?? hexData) &&
        new GeoJsonLayer({
          id: 'cells',
          data: (cellFill ?? hexData) as FeatureCollection,
          filled: true,
          stroked: false,
          getFillColor: (f: Feature) => rgb(String(f.properties?.color ?? '#888888'), 150),
        }),
      showStations &&
        stations &&
        new GeoJsonLayer({
          id: 'stations',
          data: stations,
          pointType: 'circle',
          getPointRadius: 6,
          pointRadiusUnits: 'pixels',
          getFillColor: (f: Feature) =>
            rgb(POWER_BAND_COLORS[String(f.properties?.power_band)] ?? '#9e9e9e'),
          getLineColor: [255, 255, 255, 255],
          getLineWidth: 1,
          lineWidthUnits: 'pixels',
          pickable: true,
          onClick: (info: { object?: Feature }) =>
            onSelectStation?.(String(info.object?.properties?.id ?? '') || null),
        }),
      sites &&
        new ScatterplotLayer<Feature<Point, SiteProperties>>({
          id: 'sites',
          data: sites.features,
          getPosition: (f) => f.geometry.coordinates as [number, number],
          getRadius: (f) =>
            f.properties.id === selectedSiteId ? 9 : f.properties.selected ? 7 : 4,
          radiusUnits: 'pixels',
          getFillColor: (f) => rgb(siteColor(f.properties)),
          getLineColor: [255, 255, 255, 255],
          getLineWidth: 1.5,
          lineWidthUnits: 'pixels',
          stroked: true,
          pickable: true,
          onClick: (info) => onSelectSite?.(info.object?.properties.id ?? null),
          updateTriggers: { getRadius: selectedSiteId },
        }),
      places.length > 0 &&
        new ScatterplotLayer<GooglePlace>({
          id: 'google-places',
          data: places,
          getPosition: (p) => [p.lng, p.lat],
          getRadius: 6,
          radiusUnits: 'pixels',
          getFillColor: rgb('#E69F00'),
          getLineColor: [255, 255, 255, 255],
          getLineWidth: 1.5,
          lineWidthUnits: 'pixels',
          stroked: true,
          pickable: true,
        }),
    ].filter(Boolean)
    overlay.setProps({
      layers,
      getTooltip: ({ object }: { object?: unknown }) => {
        const o = object as (GooglePlace & { properties?: Record<string, unknown> }) | undefined
        if (!o) return null
        if ('place_id' in o) return `${o.name} (${o.category.toLowerCase()}) · Google`
        const p = o.properties
        return p ? String(p.name ?? p.host_type ?? 'Site') : null
      },
    })
  }, [
    overlay,
    hexData,
    cellFill,
    stations,
    showStations,
    sites,
    selectedSiteId,
    places,
    onSelectSite,
    onSelectStation,
  ])
  return null
}

export function GoogleLayersMap(
  props: Props & { center?: { lat: number; lng: number }; zoom?: number },
) {
  const key = import.meta.env.VITE_GOOGLE_MAPS_BROWSER_KEY as string | undefined
  const [places, setPlaces] = useState<GooglePlace[]>([])
  const [placesError, setPlacesError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const selected = props.sites?.features.find((f) => f.properties.id === props.selectedSiteId)
  useEffect(() => setPlaces([]), [props.selectedSiteId])
  if (!key) {
    return (
      <div role="status" className="flex h-full items-center justify-center p-6 text-sm text-muted">
        Google Maps isn't configured: set VITE_GOOGLE_MAPS_BROWSER_KEY in .env and restart the web
        app.
      </div>
    )
  }
  const loadPlaces = () => {
    if (!selected) return
    const [lng, lat] = selected.geometry.coordinates
    setLoading(true)
    setPlacesError(null)
    getJson<{ places: GooglePlace[] }>(`/google/places/nearby?lat=${lat}&lng=${lng}&radius_m=500`)
      .then((r) => setPlaces(r.places))
      .catch((e: Error) => setPlacesError(e.message))
      .finally(() => setLoading(false))
  }
  return (
    <div className="relative h-full">
      <APIProvider apiKey={key}>
        <Map
          key={`${props.center?.lat ?? 'd'}:${props.zoom ?? 11}`}
          defaultCenter={props.center ?? PMR_CENTER}
          defaultZoom={props.zoom ?? 11}
          gestureHandling="greedy"
          streetViewControl={false}
          mapTypeControl
          style={{ width: '100%', height: '100%' }}
        >
          <Overlay {...props} places={places} />
        </Map>
      </APIProvider>
      {selected && (
        <div className="absolute left-3 top-16 z-10 w-64 rounded border border-line bg-panel/95 p-2 text-xs shadow">
          <button
            type="button"
            onClick={loadPlaces}
            disabled={loading}
            className="w-full rounded bg-ink px-2 py-1 text-paper disabled:opacity-50"
          >
            {loading ? 'Asking Google…' : 'Google places within 500 m'}
          </button>
          {placesError && (
            <p role="alert" className="mt-1 text-warn">
              {placesError}
            </p>
          )}
          {places.length > 0 && (
            <ul className="mt-1 max-h-56 overflow-y-auto" aria-label="Google places">
              {places.map((p) => (
                <li key={p.place_id} className="border-t border-line py-0.5">
                  {p.name}{' '}
                  <span className="text-muted">· {p.category.toLowerCase().replace('_', ' ')}</span>
                </li>
              ))}
            </ul>
          )}
          <p className="mt-1 text-[10px] text-muted">
            Live from Google (a cost-guarded paid call), shown only on this map, not stored. Places
            data © Google.
          </p>
        </div>
      )}
    </div>
  )
}

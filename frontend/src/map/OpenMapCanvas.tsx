import { useEffect, useRef, useState } from 'react'
import type { FeatureCollection, Point } from 'geojson'
import * as maplibregl from 'maplibre-gl'
import type { Map as MapLibreMap } from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import { API_URL, apiFetch, type H3Layer, type SiteProperties } from '../lib/api'
import { authHeaders } from '../lib/auth'
import { RULE_LABELS, siteTitle } from '../lib/planning'
import { hexClasses, hexGeoJson, type HexKind } from './hexLayer'
import { canRenderOn, type LicenseClass } from './licenseGuard'
import { POWER_BAND_COLORS } from './powerBands'

const MARTIN_URL = import.meta.env.VITE_MARTIN_URL ?? 'http://localhost:3000'

// Pune Metropolitan Region centre (Section 1: MVP geography).
const PMR_CENTER: [number, number] = [73.8567, 18.5204]

const ALL_LICENSES: LicenseClass[] = [
  'OPEN',
  'GOVERNMENT',
  'SYNTHETIC',
  'RESTRICTED_GOOGLE',
  'RESTRICTED_COMMERCIAL',
]
// Enforced in the map style itself, so no code path can draw a restricted feature here.
const OPEN_CANVAS_LICENSES = ALL_LICENSES.filter((l) => canRenderOn(l, 'open'))

/** Candidate-site status colours (colour-blind-safe pair; the accent stays reserved for selection). */
export const SITE_COLORS = {
  feasible: '#2166ac',
  rejected: '#b35806',
  proposed: '#5f6b73',
  // Plan (Phase 6), Okabe-Ito: selected/kept, added by a what-if, dropped by it.
  selected: '#0072B2',
  added: '#009E73',
  removed: '#D55E00',
} as const

interface Props {
  selectedStationId?: string | null
  onSelectStation?: (id: string | null) => void
  /** Demand or gap per H3 cell, drawn beneath the station layer. */
  hex?: { data: H3Layer; kind: HexKind } | null
  /** Candidate sites of a planning run (Phase 4), coloured by feasibility status. */
  sites?: FeatureCollection<Point, SiteProperties> | null
  selectedSiteId?: string | null
  onSelectSite?: (id: string | null) => void
  /** API path of the selected site's drive-time catchment polygon. */
  catchmentUrl?: string | null
  /** Pre-coloured H3 cells (module D catchments); replaces `hex` when set. */
  cellFill?: FeatureCollection | null
}

const EMPTY: FeatureCollection = { type: 'FeatureCollection', features: [] }

/**
 * Open-data map mode (ADR 0002): MapLibre + an OpenStreetMap-based basemap
 * (OpenFreeMap, no key), OSM-derived layers served as MVT from Martin, and
 * fused charging stations from the API.
 */
export function OpenMapCanvas({
  selectedStationId = null,
  onSelectStation,
  hex = null,
  sites = null,
  selectedSiteId = null,
  onSelectSite,
  catchmentUrl = null,
  cellFill = null,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<MapLibreMap | null>(null)
  const onSelectRef = useRef(onSelectStation)
  const onSelectSiteRef = useRef(onSelectSite)
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    onSelectRef.current = onSelectStation
    onSelectSiteRef.current = onSelectSite
  }, [onSelectStation, onSelectSite])

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return

    const map = new maplibregl.Map({
      container: containerRef.current,
      style: 'https://tiles.openfreemap.org/styles/liberty',
      center: PMR_CENTER,
      zoom: 11,
      attributionControl: { compact: true },
      // Our API needs the signed-in user's token; tile hosts don't get it.
      transformRequest: (url) =>
        url.startsWith(API_URL) ? { url, headers: authHeaders() } : { url },
    })
    mapRef.current = map
    map.addControl(new maplibregl.NavigationControl(), 'top-right')

    map.on('load', () => {
      const osm = '© OpenStreetMap contributors'
      map.addSource('road_segment', {
        type: 'vector',
        tiles: [`${MARTIN_URL}/road_segment/{z}/{x}/{y}`],
        minzoom: 8,
        maxzoom: 16,
        attribution: osm,
      })
      map.addLayer({
        id: 'road_segment',
        type: 'line',
        source: 'road_segment',
        'source-layer': 'road_segment',
        paint: { 'line-color': '#5f6b73', 'line-width': 0.6, 'line-opacity': 0.6 },
      })

      map.addSource('admin_boundary', {
        type: 'vector',
        tiles: [`${MARTIN_URL}/admin_boundary/{z}/{x}/{y}`],
        minzoom: 4,
        maxzoom: 14,
        attribution: osm,
      })
      map.addLayer({
        id: 'admin_boundary-line',
        type: 'line',
        source: 'admin_boundary',
        'source-layer': 'admin_boundary',
        paint: { 'line-color': '#13212b', 'line-width': 1.2, 'line-dasharray': [3, 2] },
      })

      map.addSource('poi', {
        type: 'vector',
        tiles: [`${MARTIN_URL}/poi/{z}/{x}/{y}`],
        minzoom: 12,
        maxzoom: 16,
        attribution: osm,
      })
      map.addLayer({
        id: 'poi-point',
        type: 'circle',
        source: 'poi',
        'source-layer': 'poi',
        // Chargers are drawn from the fused station layer, not raw OSM.
        filter: ['!=', ['get', 'category'], 'EV_CHARGER'],
        paint: { 'circle-radius': 2, 'circle-color': '#5f6b73', 'circle-opacity': 0.5 },
      })

      map.addSource('hex', { type: 'geojson', data: EMPTY })
      map.addLayer({
        id: 'hex-fill',
        type: 'fill',
        source: 'hex',
        paint: { 'fill-color': ['get', 'color'], 'fill-opacity': 0.7 },
      })

      map.addSource('stations', {
        type: 'geojson',
        data: `${API_URL}/api/v1/stations`,
        promoteId: 'id',
      })
      const licenseFilter: maplibregl.FilterSpecification = [
        'in',
        ['get', 'license_class'],
        ['literal', OPEN_CANVAS_LICENSES],
      ]
      map.addLayer({
        id: 'stations-selected',
        type: 'circle',
        source: 'stations',
        filter: ['all', licenseFilter, ['==', ['get', 'id'], '']],
        paint: {
          'circle-radius': 13,
          'circle-color': 'transparent',
          'circle-stroke-width': 3,
          'circle-stroke-color': '#d9480f',
        },
      })
      map.addLayer({
        id: 'stations',
        type: 'circle',
        source: 'stations',
        filter: licenseFilter,
        paint: {
          'circle-radius': ['interpolate', ['linear'], ['zoom'], 9, 4, 14, 8],
          // Spread match arms can't be typed as a tuple; the shape is ['match', input, k, v, ..., fallback].
          'circle-color': [
            'match',
            ['get', 'power_band'],
            ...Object.entries(POWER_BAND_COLORS).flat(),
            POWER_BAND_COLORS.Unknown,
          ] as unknown as maplibregl.ExpressionSpecification,
          // Confidence as a visual encoding (Section 12.4), not a footnote.
          'circle-opacity': ['match', ['get', 'confidence'], 'HIGH', 1, 'MEDIUM', 0.85, 0.45],
          'circle-stroke-width': ['match', ['get', 'confidence'], 'HIGH', 2, 1],
          'circle-stroke-color': '#ffffff',
        },
      })

      map.addSource('catchment', { type: 'geojson', data: EMPTY })
      map.addLayer({
        id: 'catchment-fill',
        type: 'fill',
        source: 'catchment',
        paint: { 'fill-color': '#d9480f', 'fill-opacity': 0.08 },
      })
      map.addLayer({
        id: 'catchment-line',
        type: 'line',
        source: 'catchment',
        paint: { 'line-color': '#d9480f', 'line-width': 1.5, 'line-dasharray': [2, 1] },
      })

      map.addSource('sites', { type: 'geojson', data: EMPTY })
      map.addLayer({
        id: 'sites-selected',
        type: 'circle',
        source: 'sites',
        filter: ['==', ['get', 'id'], ''],
        paint: {
          'circle-radius': 11,
          'circle-color': 'transparent',
          'circle-stroke-width': 3,
          'circle-stroke-color': '#d9480f',
        },
      })
      map.addLayer({
        id: 'sites',
        type: 'circle',
        source: 'sites',
        paint: {
          'circle-radius': [
            'interpolate',
            ['linear'],
            ['zoom'],
            9,
            ['case', ['to-boolean', ['get', 'plan']], 7, ['boolean', ['get', 'top'], false], 6, 3],
            14,
            [
              'case',
              ['to-boolean', ['get', 'plan']],
              12,
              ['boolean', ['get', 'top'], false],
              11,
              7,
            ],
          ],
          // Plan status (Phase 6) wins; then score shading; otherwise status.
          'circle-color': [
            'case',
            ['==', ['get', 'plan'], 'added'],
            SITE_COLORS.added,
            ['==', ['get', 'plan'], 'removed'],
            SITE_COLORS.removed,
            ['any', ['==', ['get', 'plan'], 'kept'], ['==', ['get', 'plan'], 'selected']],
            SITE_COLORS.selected,
            ['all', ['==', ['get', 'status'], 'feasible'], ['to-boolean', ['get', 'score_total']]],
            [
              'interpolate',
              ['linear'],
              ['to-number', ['get', 'score_total']],
              30,
              '#c6dbef',
              85,
              SITE_COLORS.feasible,
            ],
            [
              'match',
              ['get', 'status'],
              'feasible',
              SITE_COLORS.feasible,
              'rejected',
              SITE_COLORS.rejected,
              SITE_COLORS.proposed,
            ],
          ],
          // Rejected sites stay visible but recede, so the reason is one hover away.
          'circle-opacity': ['match', ['get', 'status'], 'rejected', 0.55, 0.9],
          'circle-stroke-width': [
            'case',
            ['any', ['to-boolean', ['get', 'plan']], ['boolean', ['get', 'top'], false]],
            2,
            1,
          ],
          'circle-stroke-color': [
            'case',
            ['any', ['to-boolean', ['get', 'plan']], ['boolean', ['get', 'top'], false]],
            '#13212b',
            '#ffffff',
          ],
        },
      })
      const popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 8 })
      map.on('mousemove', 'sites', (e) => {
        const p = e.features?.[0]?.properties
        if (!p) return
        map.getCanvas().style.cursor = 'pointer'
        // GeoJSON-source arrays arrive as JSON strings in feature properties.
        const codes: string[] =
          typeof p.reason_codes === 'string' ? JSON.parse(p.reason_codes) : (p.reason_codes ?? [])
        const div = document.createElement('div')
        div.className = 'text-xs text-[#13212b]'
        const title = document.createElement('div')
        title.className = 'font-semibold'
        title.textContent = `${siteTitle({ name: p.name ?? null, host_type: p.host_type ?? null })} · ${p.status}`
        div.append(title)
        if (p.rank != null && p.score_total != null) {
          const score = document.createElement('div')
          score.textContent = `Rank ${p.rank} · score ${Number(p.score_total).toFixed(1)}`
          div.append(score)
        }
        for (const c of codes) {
          const line = document.createElement('div')
          line.textContent = `${c}: ${RULE_LABELS[c] ?? c}`
          div.append(line)
        }
        const note = document.createElement('div')
        note.className = 'text-[#5f6b73]'
        note.textContent = p.label
        div.append(note)
        popup.setLngLat(e.lngLat).setDOMContent(div).addTo(map)
      })
      map.on('mouseleave', 'sites', () => {
        map.getCanvas().style.cursor = ''
        popup.remove()
      })
      map.on('click', 'sites', (e) => {
        const id = e.features?.[0]?.properties?.id as string | undefined
        onSelectSiteRef.current?.(id ?? null)
      })

      map.on('click', 'stations', (e) => {
        const id = e.features?.[0]?.properties?.id as string | undefined
        onSelectRef.current?.(id ?? null)
      })
      map.on('mouseenter', 'stations', () => (map.getCanvas().style.cursor = 'pointer'))
      map.on('mouseleave', 'stations', () => (map.getCanvas().style.cursor = ''))
      setLoaded(true)
    })

    return () => {
      map.remove()
      mapRef.current = null
    }
  }, [])

  useEffect(() => {
    const source = mapRef.current?.getSource('hex') as maplibregl.GeoJSONSource | undefined
    if (!loaded || !source) return
    source.setData(
      cellFill ?? (hex ? hexGeoJson(hex.data, hexClasses(hex.data.value, hex.kind)) : EMPTY),
    )
  }, [hex, cellFill, loaded])

  useEffect(() => {
    const source = mapRef.current?.getSource('sites') as maplibregl.GeoJSONSource | undefined
    if (!loaded || !source) return
    source.setData(sites ?? EMPTY)
  }, [sites, loaded])

  useEffect(() => {
    const source = mapRef.current?.getSource('catchment') as maplibregl.GeoJSONSource | undefined
    if (!loaded || !source) return
    if (!catchmentUrl) {
      source.setData(EMPTY)
      return
    }
    let stale = false
    apiFetch(catchmentUrl)
      .then((r) => (r.ok ? r.json() : EMPTY))
      .then((data) => {
        if (!stale) source.setData(data)
      })
      .catch(() => {
        if (!stale) source.setData(EMPTY)
      })
    return () => {
      stale = true
    }
  }, [catchmentUrl, loaded])

  useEffect(() => {
    const map = mapRef.current
    if (!loaded || !map?.getLayer('sites-selected')) return
    map.setFilter('sites-selected', ['==', ['get', 'id'], selectedSiteId ?? ''])
  }, [selectedSiteId, loaded])

  useEffect(() => {
    const map = mapRef.current
    if (!map?.getLayer('stations-selected')) return
    map.setFilter('stations-selected', [
      'all',
      ['in', ['get', 'license_class'], ['literal', OPEN_CANVAS_LICENSES]],
      ['==', ['get', 'id'], selectedStationId ?? ''],
    ])
  }, [selectedStationId])

  return <div ref={containerRef} className="h-full w-full" data-testid="open-map-canvas" />
}

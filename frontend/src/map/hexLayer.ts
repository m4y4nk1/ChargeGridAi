import { cellToBoundary } from 'h3-js'
import type { FeatureCollection } from 'geojson'
import type { H3Layer } from '../lib/api'

export type HexKind = 'demand' | 'gap'

/** ColorBrewer 5-class sequential ramps (colour-blind safe). */
export const HEX_PALETTES: Record<HexKind, string[]> = {
  demand: ['#ffffcc', '#a1dab4', '#41b6c4', '#2c7fb8', '#253494'],
  gap: ['#feebe2', '#fbb4b9', '#f768a1', '#c51b8a', '#7a0177'],
}

export interface HexClasses {
  /** Upper bound of each class except the last (length = colours - 1). */
  breaks: number[]
  colors: string[]
}

/** Quantile breaks over the positive values; zero cells are left undrawn. */
export function hexClasses(values: number[], kind: HexKind): HexClasses {
  const colors = HEX_PALETTES[kind]
  const positive = values.filter((v) => v > 0).sort((a, b) => a - b)
  if (positive.length === 0) return { breaks: [], colors }
  const breaks = colors
    .slice(0, -1)
    .map((_, i) => positive[Math.floor(((i + 1) * positive.length) / colors.length) - 1] ?? 0)
  return { breaks: [...new Set(breaks)], colors }
}

export function classify(value: number, classes: HexClasses): string {
  const i = classes.breaks.findIndex((b) => value <= b)
  return classes.colors[i === -1 ? classes.breaks.length : i]
}

export function hexGeoJson(layer: H3Layer, classes: HexClasses): FeatureCollection {
  return {
    type: 'FeatureCollection',
    features: layer.h3.flatMap((cell, i) => {
      const value = layer.value[i]
      if (!(value > 0)) return []
      const ring = cellToBoundary(cell, true)
      return [
        {
          type: 'Feature' as const,
          geometry: { type: 'Polygon' as const, coordinates: [[...ring, ring[0]]] },
          properties: { h3: cell, value, color: classify(value, classes) },
        },
      ]
    }),
  }
}

// Okabe-Ito plus Tol muted: 16 distinguishable categorical colours for stations.
export const CATEGORY_COLORS = [
  '#0072B2',
  '#E69F00',
  '#009E73',
  '#CC79A7',
  '#56B4E9',
  '#D55E00',
  '#F0E442',
  '#882255',
  '#44AA99',
  '#999933',
  '#AA4499',
  '#117733',
  '#332288',
  '#DDCC77',
  '#88CCEE',
  '#661100',
]

/** One polygon per cell coloured by `colorOf(cell)`; cells mapped to null are skipped. */
export function categoricalCells(
  cells: Record<string, string>,
  colorOf: (key: string) => string | null,
): FeatureCollection {
  return {
    type: 'FeatureCollection',
    features: Object.entries(cells).flatMap(([cell, key]) => {
      const color = colorOf(key)
      if (!color) return []
      const ring = cellToBoundary(cell, true)
      return [
        {
          type: 'Feature' as const,
          geometry: { type: 'Polygon' as const, coordinates: [[...ring, ring[0]]] },
          properties: { h3: cell, key, color },
        },
      ]
    }),
  }
}

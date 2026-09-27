import { describe, expect, it } from 'vitest'
import type { Polygon } from 'geojson'
import type { H3Layer } from '../lib/api'
import { HEX_PALETTES, classify, hexClasses, hexGeoJson } from './hexLayer'

const layer = (values: number[]): H3Layer => ({
  year: 2030,
  scenario: 'base',
  metric: 'gap_kwh',
  unit: 'kWh/day',
  h3: values.map(() => '8860885017fffff'),
  value: values,
  confidence: 'NONE',
})

describe('hexLayer', () => {
  it('splits positive values into quantile classes and ignores zeros', () => {
    const classes = hexClasses([0, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 'demand')
    expect(classes.breaks).toEqual([2, 4, 6, 8])
    expect(classify(1, classes)).toBe(HEX_PALETTES.demand[0])
    expect(classify(10, classes)).toBe(HEX_PALETTES.demand[4])
  })

  it('has no breaks when nothing is positive', () => {
    expect(hexClasses([0, 0], 'gap').breaks).toEqual([])
  })

  it('draws only cells with a positive value, as closed polygons', () => {
    const data = layer([0, 5])
    const fc = hexGeoJson(data, hexClasses(data.value, 'gap'))
    expect(fc.features).toHaveLength(1)
    const ring = (fc.features[0].geometry as Polygon).coordinates[0]
    expect(ring[0]).toEqual(ring[ring.length - 1])
    expect(ring[0][0]).toBeCloseTo(73.86, 1) // [lng, lat] order: longitude first
  })
})

import { describe, expect, it } from 'vitest'
import { describeRule, funnelStages, parseUserSites, siteTitle } from './planning'

describe('parseUserSites', () => {
  it('parses lat, lng and an optional name containing commas', () => {
    const { sites, errors } = parseUserSites('18.53, 73.85, Depot, Gate 2\n\n# comment\n18.6,73.9')
    expect(errors).toEqual([])
    expect(sites).toEqual([
      { lat: 18.53, lng: 73.85, name: 'Depot, Gate 2' },
      { lat: 18.6, lng: 73.9, name: null },
    ])
  })

  it('reports bad lines by number instead of dropping them silently', () => {
    const { sites, errors } = parseUserSites('18.5\nabc, 73\n95, 73')
    expect(sites).toEqual([])
    expect(errors).toEqual([
      'Line 1: expected "lat, lng, name"',
      'Line 2: expected "lat, lng, name"',
      'Line 3: coordinates out of range',
    ])
  })
})

describe('describeRule', () => {
  it('states the measurement against its limit', () => {
    expect(describeRule({ outcome: 'fail', measured: 1263.3, threshold: 300, unit: 'm' })).toBe(
      'measured 1,263.3 m (limit 300 m)',
    )
  })

  it('names restricted land hits', () => {
    expect(
      describeRule({
        outcome: 'fail',
        measured: [
          { class: 'forest', name: 'Katraj Ghat' },
          { class: 'military', name: null },
        ],
      }),
    ).toBe('forest: Katraj Ghat; military')
  })

  it('keeps notes and extra measured fields', () => {
    expect(
      describeRule({
        outcome: 'not_applied',
        measured: 4602.1,
        unit: 'm',
        note: 'grid data confidence LOW',
        access_ratio: 0.5,
      }),
    ).toBe('measured 4,602.1 m · grid data confidence LOW · access ratio 0.5')
  })
})

describe('funnelStages', () => {
  it('marks later-phase stages as pending rather than zero', () => {
    const stages = funnelStages({
      theoretical: { total: 2730, by_origin: {} },
      candidates: { total: 1306, by_origin: {}, merged_within_m: 150, removed_as_duplicates: 1401 },
      feasible: { total: 1175, by_origin: {} },
      shortlisted: null,
      selected: null,
    })
    expect(stages.map((s) => s.value)).toEqual([2730, 1306, 1175, null, null])
    expect(stages[3].pending).toMatch(/Phase 5/)
    expect(stages[4].pending).toMatch(/Phase 6/)
  })
})

describe('siteTitle', () => {
  it('prefers the host name, then describes the host type', () => {
    expect(siteTitle({ name: 'Amanora Mall', host_type: 'MALL' })).toBe('Amanora Mall')
    expect(siteTitle({ name: null, host_type: 'FUEL_STATION' })).toBe('Unnamed fuel station')
    expect(siteTitle({ name: null, host_type: 'ROADSIDE' })).toBe('Roadside point')
  })
})

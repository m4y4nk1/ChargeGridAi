import { describe, expect, it } from 'vitest'
import { canRenderOn, filterLayersForCanvas, requiredAttributions } from './licenseGuard'

describe('licenseGuard', () => {
  it('allows OPEN, GOVERNMENT, and SYNTHETIC content on either canvas', () => {
    for (const licenseClass of ['OPEN', 'GOVERNMENT', 'SYNTHETIC'] as const) {
      expect(canRenderOn(licenseClass, 'google')).toBe(true)
      expect(canRenderOn(licenseClass, 'open')).toBe(true)
    }
  })

  it('confines RESTRICTED_GOOGLE content to the Google canvas', () => {
    expect(canRenderOn('RESTRICTED_GOOGLE', 'google')).toBe(true)
    expect(canRenderOn('RESTRICTED_GOOGLE', 'open')).toBe(false)
  })

  it('never renders RESTRICTED_COMMERCIAL content on our canvases (Mappls forbids co-display entirely)', () => {
    expect(canRenderOn('RESTRICTED_COMMERCIAL', 'google')).toBe(false)
    expect(canRenderOn('RESTRICTED_COMMERCIAL', 'open')).toBe(false)
  })

  it('filters a mixed layer set down to what the open canvas may show', () => {
    const layers = [
      { id: 'roads', licenseClass: 'OPEN' as const },
      { id: 'places', licenseClass: 'RESTRICTED_GOOGLE' as const },
      { id: 'traffic', licenseClass: 'RESTRICTED_COMMERCIAL' as const },
    ]
    expect(filterLayersForCanvas(layers, 'open').map((l) => l.id)).toEqual(['roads'])
    expect(filterLayersForCanvas(layers, 'google').map((l) => l.id)).toEqual(['roads', 'places'])
  })

  it('collects only the attributions needed for the layers actually shown', () => {
    const layers = [
      { licenseClass: 'OPEN' as const },
      { licenseClass: 'OPEN' as const },
      { licenseClass: 'GOVERNMENT' as const },
    ]
    expect(requiredAttributions(layers)).toEqual([
      '© OpenStreetMap contributors',
      'Government of India / State data sources',
    ])
  })
})

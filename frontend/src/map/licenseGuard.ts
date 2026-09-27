/**
 * Licence enforcement for map rendering (ADR 0002; Section 3/7.4 of the
 * project brief; corrected per docs/verification.md item 8).
 *
 * - RESTRICTED_GOOGLE content may render only on the Google Maps canvas.
 * - RESTRICTED_COMMERCIAL (Mappls, TomTom) may render ONLY on its own
 *   provider's canvas — Mappls' own terms forbid co-display with any
 *   non-Mappls map entirely, so unlike Google there is no shared "open
 *   canvas with attribution" allowance at all. We have no Mappls-branded
 *   canvas in this app, so RESTRICTED_COMMERCIAL never renders here.
 * - OPEN, GOVERNMENT, and SYNTHETIC content may render on either canvas.
 */

export type LicenseClass =
  'OPEN' | 'GOVERNMENT' | 'RESTRICTED_GOOGLE' | 'RESTRICTED_COMMERCIAL' | 'SYNTHETIC'

export type MapCanvas = 'google' | 'open'

const ATTRIBUTIONS: Record<LicenseClass, string | null> = {
  OPEN: '© OpenStreetMap contributors',
  GOVERNMENT: 'Government of India / State data sources',
  RESTRICTED_GOOGLE: '© Google',
  RESTRICTED_COMMERCIAL: null,
  SYNTHETIC: 'Synthetic data — not for operational use',
}

export function canRenderOn(licenseClass: LicenseClass, canvas: MapCanvas): boolean {
  if (licenseClass === 'RESTRICTED_GOOGLE') return canvas === 'google'
  if (licenseClass === 'RESTRICTED_COMMERCIAL') return false
  return true
}

export function attributionFor(licenseClass: LicenseClass): string | null {
  return ATTRIBUTIONS[licenseClass]
}

/** Filters a set of layers (each carrying a licenseClass) down to what a given canvas may show. */
export function filterLayersForCanvas<T extends { licenseClass: LicenseClass }>(
  layers: T[],
  canvas: MapCanvas,
): T[] {
  return layers.filter((layer) => canRenderOn(layer.licenseClass, canvas))
}

/** Distinct attribution strings required for a set of layers actually being rendered. */
export function requiredAttributions<T extends { licenseClass: LicenseClass }>(
  layers: T[],
): string[] {
  const seen = new Set<string>()
  for (const layer of layers) {
    const attribution = attributionFor(layer.licenseClass)
    if (attribution) seen.add(attribution)
  }
  return [...seen]
}

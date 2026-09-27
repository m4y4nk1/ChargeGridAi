/** Sequential single-hue scale (ColorBrewer PuBu) — ordered by power, colour-blind safe. */
export const POWER_BAND_COLORS: Record<string, string> = {
  AC: '#a6bddb',
  'DC <60 kW': '#74a9cf',
  'DC 60–149 kW': '#3690c0',
  'DC 150–349 kW': '#0570b0',
  'DC ≥350 kW': '#034e7b',
  Unknown: '#9e9e9e',
}

export const POWER_BAND_ORDER = Object.keys(POWER_BAND_COLORS)

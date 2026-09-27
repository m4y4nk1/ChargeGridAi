import { useEffect, useState } from 'react'

/**
 * Chart colours per colour scheme. ECharts can't read CSS variables, so each mode's
 * steps are listed here; both pairs pass the dataviz palette validator (lightness
 * band, chroma, CVD and normal-vision separation, contrast) against their surface.
 */
const PALETTES = {
  light: {
    cool: '#0072B2', // above base / primary series
    warm: '#D55E00', // below base / contrast series
    ink: '#13212b',
    muted: '#5f6b73',
    grid: '#dcd8cf',
    surface: '#ffffff',
    // Energy supply mix (validated as a set): solar, battery, grid.
    solar: '#C98A00',
    battery: '#009E73',
    mains: '#0072B2',
    // Categorical series in fixed order (validated as a set, 5 slots; light-mode
    // contrast WARN -> charts using it carry a legend and a table view).
    series: ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4'],
  },
  dark: {
    cool: '#3a9ad9',
    warm: '#d46a26',
    ink: '#e8e6e1',
    muted: '#9aa6ad',
    grid: '#2e3a41',
    surface: '#1a2227',
    solar: '#b8841a',
    battery: '#23a67e',
    mains: '#3a9ad9',
    series: ['#3987e5', '#d95926', '#199e70', '#c98500', '#d55181'],
  },
} as const

export type ChartPalette = (typeof PALETTES)[keyof typeof PALETTES]

function darkQuery(): MediaQueryList | null {
  return typeof window !== 'undefined' && window.matchMedia
    ? window.matchMedia('(prefers-color-scheme: dark)')
    : null
}

export function useChartColors(): ChartPalette {
  const [dark, setDark] = useState(() => !!darkQuery()?.matches)
  useEffect(() => {
    const query = darkQuery()
    if (!query) return
    const onChange = (e: MediaQueryListEvent) => setDark(e.matches)
    query.addEventListener('change', onChange)
    return () => query.removeEventListener('change', onChange)
  }, [])
  return dark ? PALETTES.dark : PALETTES.light
}

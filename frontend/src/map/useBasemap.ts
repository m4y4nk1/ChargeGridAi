import { useState } from 'react'

export type Basemap = 'open' | 'google'
const KEY = 'chargegrid.basemap'

/** Which basemap to show; remembered per browser (a convenience, not state). */
export function useBasemap(): [Basemap, (b: Basemap) => void] {
  const available = !!import.meta.env.VITE_GOOGLE_MAPS_BROWSER_KEY
  const [value, setValue] = useState<Basemap>(() => {
    try {
      return available && localStorage.getItem(KEY) === 'google' ? 'google' : 'open'
    } catch {
      return 'open'
    }
  })
  const set = (b: Basemap) => {
    setValue(b)
    try {
      localStorage.setItem(KEY, b)
    } catch {
      /* storage unavailable */
    }
  }
  return [available ? value : 'open', set]
}

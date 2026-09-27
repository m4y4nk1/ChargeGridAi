import type { Basemap } from './useBasemap'

export function BasemapToggle({
  value,
  onChange,
}: {
  value: Basemap
  onChange: (b: Basemap) => void
}) {
  if (!import.meta.env.VITE_GOOGLE_MAPS_BROWSER_KEY) return null
  return (
    <div
      role="radiogroup"
      aria-label="Basemap"
      className="absolute left-1/2 top-3 z-10 flex -translate-x-1/2 rounded border border-line bg-panel/95 text-xs shadow"
    >
      {(
        [
          ['open', 'OpenStreetMap'],
          ['google', 'Google Maps'],
        ] as const
      ).map(([b, label]) => (
        <button
          key={b}
          type="button"
          role="radio"
          aria-checked={value === b}
          onClick={() => onChange(b)}
          className={`px-2 py-1 ${value === b ? 'bg-ink text-paper' : ''}`}
        >
          {label}
        </button>
      ))}
    </div>
  )
}

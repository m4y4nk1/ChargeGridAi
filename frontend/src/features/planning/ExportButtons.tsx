/** Licence-filtered plan exports (GeoJSON, XLSX, PDF) with an attribution sheet. */
import { useState } from 'react'
import { downloadFile } from '../../lib/api'

const FORMATS = [
  ['xlsx', 'Excel'],
  ['pdf', 'PDF'],
  ['geojson', 'GeoJSON'],
] as const

export function ExportButtons({ runId }: { runId: string }) {
  const [error, setError] = useState<string | null>(null)
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs" aria-label="Export">
      <span className="text-muted">Export plan:</span>
      {FORMATS.map(([fmt, label]) => (
        <button
          key={fmt}
          type="button"
          className="rounded border border-line px-2 py-0.5 hover:bg-panel"
          onClick={() => {
            setError(null)
            downloadFile(`/exports/runs/${runId}.${fmt}`, `chargegrid-plan.${fmt}`).catch(
              (e: Error) => setError(e.message),
            )
          }}
        >
          {label}
        </button>
      ))}
      {error && (
        <span role="alert" className="text-warn">
          {error}
        </span>
      )}
    </div>
  )
}

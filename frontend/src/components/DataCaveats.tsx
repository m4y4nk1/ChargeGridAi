import type { DemandRun } from '../lib/api'

/**
 * Section 0 rule 4 and Section 16: when figures rest on synthetic data or on
 * illustrative placeholder parameters, say so above them, not in a footnote.
 */
export function DataCaveats({ run }: { run: DemandRun }) {
  const placeholders = run.placeholders_in_use
  if (!run.synthetic_registrations && placeholders.length === 0) return null

  return (
    <div role="note" className="flex flex-col gap-2">
      {run.synthetic_registrations && (
        <div className="rounded border border-synthetic bg-synthetic/10 px-3 py-2 text-sm">
          <strong className="text-synthetic">Illustrative only.</strong> EV registrations are a
          synthetic stand-in ({run.registration_sources.join(', ')}), not VAHAN data, so every EV,
          demand and gap figure below is a demonstration of the method rather than an estimate.
        </div>
      )}
      {placeholders.length > 0 && (
        <details className="rounded border border-warn bg-warn/10 px-3 py-2 text-sm">
          <summary className="cursor-pointer">
            <strong className="text-warn">
              {placeholders.length} placeholder assumption{placeholders.length === 1 ? '' : 's'} in
              use.
            </strong>{' '}
            Replace them in <code className="font-mono text-xs">config/</code> with sourced values.
          </summary>
          <ul className="mt-2 columns-2 font-mono text-xs text-muted">
            {placeholders.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
        </details>
      )}
    </div>
  )
}

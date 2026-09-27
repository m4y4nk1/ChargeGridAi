import { useQuery } from '@tanstack/react-query'
import { FreshnessBadge, LicenseBadge } from '../../components/badges'
import { getJson, type DataSource } from '../../lib/api'
import { formatCount, formatDate } from '../../lib/format'
import { RegionReadiness } from './RegionReadiness'

function QualityDetails({ label, report }: { label: string; report: Record<string, unknown> }) {
  return (
    <details className="mt-1">
      <summary className="cursor-pointer text-xs text-muted">Quality report · {label}</summary>
      <pre className="mt-1 max-h-60 overflow-auto rounded bg-paper p-2 font-mono text-[11px]">
        {JSON.stringify(report, null, 2)}
      </pre>
    </details>
  )
}

/** Data catalogue (Section 12.1 `/data`): every source, its licence, and how fresh it is. */
export function DataCataloguePage() {
  const { data, isError, isLoading } = useQuery({
    queryKey: ['data-sources'],
    queryFn: () => getJson<DataSource[]>('/data-sources'),
  })

  return (
    <main className="mx-auto max-w-6xl p-6">
      <h1 className="text-xl font-semibold">Data catalogue</h1>
      <p className="mt-1 text-sm text-muted">
        Every source behind the numbers in ChargeGrid AI: its licence, how often it should be
        refreshed, and when it was last ingested.
      </p>

      <RegionReadiness />

      {isLoading && <p className="mt-6 text-sm text-muted">Loading…</p>}
      {isError && <p className="mt-6 text-sm text-warn">Couldn’t load the data catalogue.</p>}
      {data && (
        <table className="mt-6 w-full border-collapse text-sm">
          <thead>
            <tr className="border-b border-ink text-left text-xs uppercase tracking-wide text-muted">
              <th className="py-2 pr-4">Source</th>
              <th className="py-2 pr-4">Licence</th>
              <th className="py-2 pr-4">Refresh</th>
              <th className="py-2 pr-4">Last ingested</th>
              <th className="py-2 pr-4 text-right">Rows</th>
              <th className="py-2">Status</th>
            </tr>
          </thead>
          <tbody>
            {data.map((s) => (
              <tr key={s.id} className="border-b border-line align-top">
                <td className="py-2 pr-4">
                  <div className="font-medium">{s.name}</div>
                  {s.attribution && <div className="text-xs text-muted">{s.attribution}</div>}
                  {s.datasets.map(
                    (d) =>
                      d.quality_report && (
                        <QualityDetails
                          key={d.id}
                          label={d.granularity ?? 'dataset'}
                          report={d.quality_report}
                        />
                      ),
                  )}
                </td>
                <td className="py-2 pr-4">
                  <LicenseBadge value={s.license_class} />
                  {s.license_text && (
                    <div className="mt-1 max-w-xs text-xs text-muted">{s.license_text}</div>
                  )}
                </td>
                <td className="py-2 pr-4 text-muted">{s.refresh_cadence ?? '—'}</td>
                <td className="py-2 pr-4">
                  {s.access === 'live' ? (
                    <span className="text-xs text-muted">
                      Fetched on use, never stored
                      {s.usage?.last_used && (
                        <>
                          <br />
                          last used {formatDate(s.usage.last_used)}
                        </>
                      )}
                    </span>
                  ) : s.datasets.length === 0 ? (
                    '—'
                  ) : (
                    s.datasets.map((d) => <div key={d.id}>{formatDate(d.retrieved_at)}</div>)
                  )}
                </td>
                <td className="py-2 pr-4 text-right tabular-nums">
                  {s.access === 'live' ? (
                    <span className="text-xs">
                      {formatCount(s.usage?.calls_this_month ?? 0)} calls this month
                    </span>
                  ) : s.datasets.length === 0 ? (
                    '—'
                  ) : (
                    s.datasets.map((d) => (
                      <div key={d.id}>
                        {formatCount(d.row_count)}{' '}
                        <span className="text-xs text-muted">{d.granularity}</span>
                      </div>
                    ))
                  )}
                </td>
                <td className="py-2">
                  <FreshnessBadge value={s.freshness} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </main>
  )
}

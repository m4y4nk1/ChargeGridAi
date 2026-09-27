import { Link } from '@tanstack/react-router'

export function OverviewPage() {
  return (
    <main className="mx-auto max-w-3xl p-6">
      <h1 className="text-2xl font-semibold">ChargeGrid AI</h1>
      <p className="mt-2 text-muted">
        EV charging infrastructure planning for the Pune Metropolitan Region.
      </p>
      <ul className="mt-6 flex flex-col gap-2">
        <li>
          <Link to="/workspace" className="underline">
            Open workspace
          </Link>{' '}
          <span className="text-sm text-muted">— existing chargers, with provenance</span>
        </li>
        <li>
          <Link to="/data" className="underline">
            Data catalogue
          </Link>{' '}
          <span className="text-sm text-muted">— sources, licences, and freshness</span>
        </li>
      </ul>
    </main>
  )
}

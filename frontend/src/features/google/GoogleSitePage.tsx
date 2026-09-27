/**
 * Google map mode for one planned site: satellite view plus the Google Solar result
 * (RESTRICTED_GOOGLE). This page is the only place that content is shown: it never
 * appears on the open MapLibre map, in exports or in agent answers.
 */
import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { getJson } from '../../lib/api'
import { formatCount, formatCrore } from '../../lib/format'
import { GoogleMapCanvas } from '../../map/GoogleMapCanvas'

interface GoogleEnergy {
  site: { id: string; name: string | null; lat: number; lng: number }
  attribution: string
  google: null | {
    recommended: string
    scenarios: Record<
      string,
      {
        label: string
        capex: Record<string, number>
        annual: { total: number }
        pv_kwp: number
        battery_kwh: number
        connection: string
      }
    >
    insight: {
      building_distance_m: number
      imagery_quality: string
      max_panels: number
      max_array_kwp: number
      max_array_area_m2: number
      yearly_dc_kwh_at_max: number
      max_sunshine_hours_per_year: number
      expires_at: string
    }
  }
  google_note: string | null
  open_recommended: string
}

interface PlanSite {
  site_id: string
  name: string | null
  lat: number
  lng: number
}

export function GoogleSitePage({
  runId,
  siteId,
  optimisationId,
}: {
  runId: string
  siteId: string
  optimisationId: string
}) {
  const q = useQuery({
    queryKey: ['energy-google', optimisationId, siteId],
    queryFn: () =>
      getJson<GoogleEnergy>(`/optimisations/${optimisationId}/sites/${siteId}/energy/google`),
  })
  const plan = useQuery({
    queryKey: ['optimisation', optimisationId],
    queryFn: () => getJson<{ sites: PlanSite[] }>(`/optimisations/${optimisationId}`),
  })
  const d = q.data
  const g = d?.google
  return (
    <div className="grid h-full grid-cols-[22rem_1fr]">
      <aside className="flex min-w-0 flex-col gap-3 overflow-y-auto border-r border-line bg-paper p-4 text-sm">
        <Link to="/runs/$runId" params={{ runId }} className="text-xs underline">
          ← Back to the plan
        </Link>
        <h1 className="text-lg font-semibold">{d?.site.name ?? 'Site'} · Google map mode</h1>
        <p className="text-xs text-muted">
          Candidate site, requires field verification. Google content on this page is shown only
          here, under Google's terms, and is deleted after 30 days.
        </p>
        {q.error && (
          <p role="alert" className="text-warn">
            {q.error.message}
          </p>
        )}
        {d && !g && (
          <p className="text-muted">{d.google_note ?? 'No Google Solar result for this site.'}</p>
        )}
        {g && (
          <>
            <section aria-label="Google Solar">
              <h2 className="font-semibold">Google Solar (nearest building)</h2>
              <dl className="mt-1 grid grid-cols-2 gap-x-2 gap-y-1 text-xs tabular-nums">
                <dt className="text-muted">Building distance</dt>
                <dd>{formatCount(g.insight.building_distance_m)} m</dd>
                <dt className="text-muted">Imagery quality</dt>
                <dd>{g.insight.imagery_quality.toLowerCase()}</dd>
                <dt className="text-muted">Max panels</dt>
                <dd>{formatCount(g.insight.max_panels)}</dd>
                <dt className="text-muted">Max array</dt>
                <dd>{formatCount(g.insight.max_array_kwp)} kWp</dd>
                <dt className="text-muted">Roof area usable</dt>
                <dd>{formatCount(g.insight.max_array_area_m2)} m²</dd>
                <dt className="text-muted">Yearly DC energy</dt>
                <dd>{formatCount(g.insight.yearly_dc_kwh_at_max)} kWh</dd>
                <dt className="text-muted">Sunshine hours</dt>
                <dd>{formatCount(g.insight.max_sunshine_hours_per_year)} h/yr</dd>
              </dl>
            </section>
            <section aria-label="Options with the Google roof">
              <h2 className="font-semibold">Options sized on this roof</h2>
              <table className="mt-1 w-full text-xs tabular-nums">
                <tbody>
                  {Object.entries(g.scenarios).map(([k, s]) => (
                    <tr key={k} className="border-t border-line">
                      <td className="py-0.5">
                        {s.label}
                        {k === g.recommended && (
                          <span className="ml-1 font-semibold">(recommended)</span>
                        )}
                      </td>
                      <td className="text-right">
                        {formatCrore(Object.values(s.capex).reduce((a, b) => a + b, 0))}
                      </td>
                      <td className="text-right">{formatCrore(s.annual.total)}/yr</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="mt-1 text-[11px] text-muted">
                Capex and annual cost; tariffs and component prices are illustrative placeholders.
                With the open canopy estimate the recommendation is “
                {d.open_recommended.replaceAll('_', ' ')}”.
              </p>
            </section>
          </>
        )}
        {d && <p className="mt-auto text-[11px] text-muted">{d.attribution}</p>}
      </aside>
      <div className="min-h-0">
        {d && (
          <GoogleMapCanvas
            center={{ lat: d.site.lat, lng: d.site.lng }}
            markers={(plan.data?.sites ?? []).map((s) => ({
              id: s.site_id,
              lat: s.lat,
              lng: s.lng,
              title: s.name ?? 'Candidate site',
              highlight: s.site_id === siteId,
            }))}
          />
        )}
      </div>
    </div>
  )
}

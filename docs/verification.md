# Verification — Section 18 findings (Phase 0)

Researched 2026-09-23 via web search against live documentation. Per Section 0
rule 3 of the project brief: where current documentation conflicts with an
assumption baked into the brief, the documentation wins and the conflict is
noted below. Items that couldn't be confirmed from public documentation alone
are marked **UNVERIFIED** with what would close them — most require either a
live API key (not available yet, see the plan's API-keys decision) or a
manual account-gated terms page. This document should be re-reviewed before
each release per Section 14.

---

## 1. Google Maps Platform: caching/storage terms + India pricing

**Caching / storage (best-effort, re-verify against the live terms page before
Phase 2 ships a Places adapter — automated fetches of the terms page returned
only a truncated excerpt):**
- Place IDs may be stored indefinitely — the one field exempted from caching
  restrictions.
- Coordinates may be cached up to 30 days.
- Everything else returned by Places (names, ratings, reviews, photos, phone
  numbers) must be requested live and displayed with Google attribution, not
  warehoused.
- General terms prohibit exporting/extracting/scraping Google Maps content for
  use outside Google's own services, except where service-specific terms
  expressly permit caching (the exemptions above).

This matches the brief's Section 3 assumption ("Google generally permits
persisting place IDs but restricts caching other content") — **no conflict**,
but treat the above as a documentation-derived summary, not a legal reading;
**UNVERIFIED at full-text level** — Legal/compliance should read the primary
source (`cloud.google.com/maps-platform/terms/maps-service-terms`) directly
before Phase 2.

**India pricing** (from `developers.google.com/maps/billing-and-pricing/...`,
current as of this check):

| SKU | Free cap (India) | Price up to 5M calls | Price above 5M |
|---|---|---|---|
| Geocoding | 70,000/mo | $1.50 / 1,000 | $0.38 / 1,000 |
| Places Autocomplete | 70,000/mo | $0.85 / 1,000 | $0.21 / 1,000 |
| Routes Navigation | 7,000/mo | $8.00 / 1,000 | $2.00 / 1,000 |
| Solar Building Insights | 70,000/mo | $4.00 / 1,000 | $3.50 / 1,000 |
| Solar Data Layers | 7,000/mo | $30.00 / 1,000 | $26.25 / 1,000 |

India pricing (the lower, India-specific rate) applies only to India-based,
India-billed customers with the large majority of usage in India — confirm
the account is configured this way before assuming these rates apply.
Solar Data Layers is notably expensive relative to Building Insights; the
energy engine (Phase 8) should default to `buildingInsights.findClosest`
and only call `dataLayers.get` when a site is shortlisted, not per candidate.

## 2. Places API (New) — EV fields and place types

**Confirmed.** The `places.evChargeOptions` field mask returns connector
count, connector type, max charge rate (kW), availability count, and
availability-last-updated time, grouped by (connector type, max charge rate)
pairs. Field masking is supported on Place Details, Text Search, and Nearby
Search — so the brief's planned `EV_DETAIL` field mask
(`places.id,places.displayName,places.location,places.types,places.evChargeOptions`)
is valid. No conflict with Section 7.2.

## 3. Google Solar API coverage for Pune/Maharashtra

**UNVERIFIED — cannot confirm without a live API key.** The public coverage
documentation states coverage is approximate and only fully queryable via the
interactive coverage map or by calling `buildingInsights.findClosest`
directly (which 404s with `NOT_FOUND` outside covered areas); it does not list
India/Maharashtra by name in static text. **Action for Phase 8:** once a
`GOOGLE_MAPS_SERVER_KEY` exists, run the 20-sample-point check the brief
specifies (Section 18 item 3) and record the NOT_FOUND share directly — do not
assume coverage either way until then. The NASA POWER / Global Solar Atlas
fallback path (Section 9.9) should be treated as the default, not a rare
fallback, until this is confirmed.

**Phase 8 update (2026-09-26):** the check is now one command,
`make solar-coverage`. It samples the latest run's 20 top-ranked sites and
prints the covered count, the NOT_FOUND share and the imagery quality. It
needs `GOOGLE_MAPS_SERVER_KEY`, `COST_CAP_MONTHLY_INR_GOOGLE` and the
INR/USD rate. The 20 calls fall within the 70,000 free units a month. The
NASA POWER path is live and is what every site uses today.

## 4. VAHAN export availability (RTO × fuel × vehicle-class × month)

**Partially verified.** The public Vahan Dashboard
(`analytics.parivahan.gov.in/analytics/publicdashboard/vahan`) supports
interactive drill-down by state, RTO, vehicle class, fuel type, and month —
but it is a dashboard, not a documented bulk-export API. data.gov.in hosts
related but coarser resources (e.g. a single-snapshot "State/UT-wise
registered EVs" release, and a third-party-maintained "VAHAN Vehicle
Registrations" dataset on India Data Portal that one indexed resource
described as annual-national-resolution, not the monthly-RTO granularity
needed). **No official documented monthly RTO-level bulk export or formal
API was found.** This confirms the brief's own caution in Section 7.1 row 3:
build the importer against manually exported dashboard CSV/XLSX, do not
scrape, and pursue a formal data-sharing request in parallel if monthly
RTO-level granularity turns out to be required before a manual-export
cadence is workable.

## 5. BEE EV Yatra / BHEL PM E-DRIVE charger data

**Confirmed: no public bulk API; manual import is the right plan.**
`evyatra.beeindia.gov.in` publishes a public charging-station list and a
per-station detail page (web UI, not an API). `evcsmhi.bhel.in` is BHEL's
*internal* project-management dashboard for PM E-DRIVE rollout coordination,
not a public data feed. This matches Section 7.1 row 5's fallback plan
(manual periodic import) exactly — **no conflict**.

## 6. Open Charge Map API — version, rate limits, licence

**Partially verified.** Confirmed: OCM's contributed data is CC BY 4.0
licensed, and v3 requires an API key for higher-volume/authenticated access.
**UNVERIFIED — specific numeric rate limits** (requests/min or /day) were not
returned by the documentation pages fetched; the per-request docs (`GET
/v3/poi?...`) should be re-checked directly against
`https://api.openchargemap.io/v3/` (or the OpenAPI spec it publishes) once an
`OCM_API_KEY` is issued, before Phase 2's ingestion job sets its polling
cadence.

## 7. Geofabrik extract for Maharashtra

**Partially confirmed, brief needs a small correction.** Geofabrik provides a
whole-of-India extract (`download.geofabrik.de/asia/india.html`,
`india-latest.osm.pbf`, ~1.6 GB, updated daily) but **no dedicated Maharashtra
state-level extract page was found** in this pass — Geofabrik's India page
lists India as a single unit rather than by state the way it does for some
other countries. **Action for Phase 1:** either (a) import the full India PBF
and filter to the PMR bounding box during `osm2pgsql` import, or (b) generate
a Maharashtra-only extract locally with `osmium extract` from the India PBF.
Update the ingestion job (Section 13, `ingest_osm`) to reflect whichever is
chosen — the brief's "Geofabrik India or Western-Zone PBF" phrasing already
anticipated this ambiguity.

## 8. Mappls API — products, auth, storage/display terms

**Confirmed, and stricter than the brief assumed.** Mappls' terms explicitly
prohibit: caching content to avoid fees, displaying Mappls search content on
a non-Mappls map, displaying Mappls and non-Mappls maps on the same screen,
and linking a Mappls map to non-Mappls map content — i.e. **no co-display
with MapLibre/OSM at all**, not just a caching restriction. Mandatory
"Powered by Mappls" branding applies wherever their content appears. Pricing
is custom, quoted from roughly $300/month for 10,000 calls. Data is stored
in-India, relevant to DPDP compliance (Section 14).

**Correction to the brief:** Section 3 point 3 and Section 7.1 row 13 treat
Mappls as "governed enrichment, similar to Google" — in practice Mappls'
co-display restriction is *more* absolute than Google's. `licenseGuard.ts`
(ADR 0002, Phase 1) must treat `RESTRICTED_COMMERCIAL` (Mappls/TomTom) content
as never renderable on the MapLibre canvas at all, not merely
attribution-gated, matching how `RESTRICTED_GOOGLE` is already scoped to the
Google canvas only.

## 9. Ministry of Power guidelines, Maharashtra EV policy, MERC tariff order

**Confirmed, more current than what the brief cited.**
- Ministry of Power: *Guidelines for Installation and Operation of Electric
  Vehicle Charging Infrastructure–2024* (issued 17 Sep 2024) is the current
  version — defines minimum standards (≥1 fast charger per station, minimum
  connector types CCS2/CHAdeMO/Type 2 AC, safety/metering, open access).
- PM E-DRIVE operational guidelines for EV Public Charging Stations (EV PCS)
  are published directly by the scheme
  (`pmedrive.heavyindustries.gov.in/docs/policy_document/EV PCS operational
  guidelines_F.pdf`) — use this as the primary citation for
  `config/policy/rules.yaml`, not a secondary summary.
- Maharashtra EV policy: capital subsidy up to 15% for DC fast-charging
  stations; mandates a charger roughly every 25 km on state highways; mandates
  Unified Energy Interface (UEI) protocol for interoperability.
- **MERC EV tariff order in force: MERC Case No. 217 of 2024** (5th control
  period order, covering FY2025-26 to FY2029-30), giving MSEDCL a dedicated
  LT/HT EV-charging tariff category with a concessional rate cited around
  ₹5–5.5/kWh and waived demand charges in the initial years; BEST's separate
  LT-VI EV tariff for FY2026-27 is cited at ₹7.43/kVAh. **Action for Phase 5:**
  populate `config/tariffs/maharashtra.yaml` from the actual MERC Case 217/2024
  order text (not the secondary sources found here) — the numbers above are
  good enough to unblock scaffolding but not to ship as source-of-truth tariff
  values.
- **Phase 9 update:** the tariff stand-ins in `config/tariffs/maharashtra.yaml`
  now follow the regime above: illustrative ₹5.5/kWh (LT) and ₹5.0/kWh (HT), with
  demand charges at 0 while they are waived. They were ₹8 / ₹7.5 per kWh and
  ₹75 / ₹350 per kVA-month, which overstated grid costs. On the corridor plan's
  first site, the median NPV moved from about −₹62 lakh to +₹2.5 crore. The
  energy recommendation changed from solar + battery on an LT connection to
  solar only, because batteries no longer pay for themselves by avoiding demand
  charges. Plans computed before the change keep their cached economics.
  The `value:` slots still need the order text, including the post-waiver
  demand charges.

## 10. Cross-check IEA / PIB figures from Section 1

- **PIB figure — confirmed, no conflict.** 52,718 public EV charging stations
  operational nationwide, including 16,561 fast chargers for cars, matches
  Section 1's "52,718 public charging stations on the BHEL portal" exactly.
  PM E-DRIVE's ₹2,000 crore allocation for public charging is also confirmed
  as stated.
- **Important nuance not in the brief:** a separate PIB-adjacent report notes
  6,562 PM E-DRIVE charging stations were *approved* (worth ₹689 crore) but
  **not yet deployed** as of the point queried — the platform's evidence
  model should be able to distinguish "sanctioned," "approved," and
  "operational" station counts, not just stations vs. charge points (Section
  7.3 point 6 already anticipates this kind of distinction; extend it to
  project-status, not only unit-of-count, when the government-charger adapter
  is built in Phase 2).
- **IEA Global EV Outlook 2026 (not 2025) is the current edition** — released
  20 May 2026, supersedes the "Global EV Outlook" edition the brief's
  85,000/520,000-by-2035 figures were presumably drawn from.
  **UNVERIFIED — exact figures:** search snippets of the 2026 edition did not
  surface the specific "~88,000 charging points end-2025" or ">520,000 by
  2035" numbers for direct comparison. **Action:** before quoting either
  figure in the UI, pull the exact numbers from the GEO 2026 PDF
  (`iea.blob.core.windows.net/.../GlobalEVOutlook2026.pdf`) or the report's
  India country page directly, and cite the 2026 edition, not "IEA reports"
  generically.

## 11. Current LLM provider model names/SDKs

**Authoritative for this session** (per this environment's own system
context, which is a more reliable source for "what model am I actually
talking to" than search-engine snippets of Anthropic's release blog):
- Anthropic: `claude-opus-5-5` (Opus 5.5), `claude-sonnet-5` (Sonnet 5),
  `claude-haiku-4-5-20251001` (Haiku 4.5), `claude-fable-5-1` (Fable 5.1,
  Mythos-class).
- These should be the values used for `LLM_MODEL_PLANNER` /
  `LLM_MODEL_REPORT` when the Anthropic adapter is built in Phase 9 — Sonnet
  5 or Opus 5.5 for planning/report generation, Haiku 4.5 for cheap
  high-volume calls (e.g. data-discovery summarization) if needed.
- OpenAI (**less certain — re-verify at Phase 9 integration time**, model
  names move faster than this doc): search results describe a September 2026
  lineup topped by "GPT-6 Astra" ($10/$50 per M tokens in/out) with a
  "GPT-5.6" mid-tier family (Sol/Terra/Luna) and older GPT-5.4/5.5 still
  listed. Treat these names as **unconfirmed against OpenAI's own current
  docs** (`developers.openai.com/api/docs/models`) — re-check directly before
  wiring the OpenAI adapter, since the brief already mandates model names come
  from env vars rather than being hardcoded (Section 4, 16), which limits the
  blast radius of this being stale.
- **Phase 9 as built:** Anthropic Python SDK 1.8.0 with LangGraph 1.2.12. The
  planner runs on `claude-opus-5-5` and the specialists and report on
  `claude-sonnet-5`, all with adaptive thinking and an explicit `effort`. List
  prices come from `config/agents.yaml` and are used for cost tracing. No
  OpenAI adapter was built (ADR 0009).

---

## Summary of conflicts / corrections vs. the brief

1. **Mappls restrictions are stricter than "governed like Google"** — no
   co-display with non-Mappls maps at all (item 8). `licenseGuard.ts` design
   in Phase 1 must reflect this.
2. **No Maharashtra-specific Geofabrik extract exists** — Phase 1's OSM
   ingestion job needs to filter the India-wide PBF instead (item 7).
3. **IEA Global EV Outlook 2026 is now the current edition**, not whatever
   earlier edition the Section 1 figures came from — re-pull exact figures
   before quoting them in the UI (item 10).
4. **VAHAN has no documented monthly RTO-level bulk export** — confirms
   rather than contradicts the brief's own caution, but forecloses assuming a
   clean API integration is available for Phase 2 (item 4).

## Outstanding UNVERIFIED items requiring live credentials

- Google Solar API coverage sample (needs `GOOGLE_MAPS_SERVER_KEY`) — item 3
- OCM numeric rate limits (needs `OCM_API_KEY`) — item 6
- Full legal text of Google Maps Platform Service Specific Terms — item 1
  (recommend legal/compliance review before Phase 2, not just this doc)
- OpenAI current model catalogue at time of Phase 9 integration — item 11

---

## Phase 2 addendum (checked 24 Sep 2026)

- **Open Charge Map:** `GET /v3/poi` without a key now returns
  `403 — You must specify an API key`. A free key is required before any
  OCM ingestion; the adapter refuses to run without `OCM_API_KEY`.
- **BEE EV Yatra:** `evyatra.beeindia.gov.in` didn't respond (connection
  timeout) during Phase 2. The government charger importer takes a manually
  exported CSV; no government charger data is loaded yet.
- **VAHAN:** still no bulk export route (item 4). A clearly labelled
  synthetic fixture (`synthetic_vahan`, licence class SYNTHETIC) stands in
  so Phase 3 can be built. Importing a real export replaces it, and the
  loader refuses to overwrite real data with synthetic.
- **WorldPop:** the data server advertises `Accept-Ranges: bytes` but
  returns the whole file for ranged requests, so windowed remote reads are
  impossible and national rasters must be downloaded, then cropped. A newer
  release than the brief assumed exists, Global2 **R2025A** (100 m,
  constrained, 2015–2030); Phase 2 uses its 2025 layer.
- **Google Places Nearby/Text Search India prices:** not captured in item 1.
  `config/costs/api_pricing.yaml` leaves them `REPLACE_ME`, which keeps the
  cost guard refusing those calls until they are filled in, together with
  the INR/USD rate.

## Google key check (27 Sep 2026, demo key)

- **Maps JavaScript API:** works. The Google basemap renders.
- **Places API (New) searchNearby:** works. Priced at the global list rate, $32 per
  1,000 after 5,000 free a month (developers.google.com/maps/billing-and-pricing/pricing);
  India-billed accounts pay the lower India list.
- **Solar API:** HTTP 403 PERMISSION_DENIED, "Solar API has not been used in project …
  or it is disabled". It must be enabled on the project. Solar stays on NASA POWER
  (`GOOGLE_SOLAR_ENABLED=false`).
- **Geocoding API:** REQUEST_DENIED until billing is enabled on the project.
- **INR/USD for the cost guard:** 95.87 (Federal Reserve H.10, 18 Sep 2026).

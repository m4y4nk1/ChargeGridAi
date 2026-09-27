# EV demand, public charging demand, and the gap (Phase 3)

Implements Sections 9.2 and 9.3 of the brief. Run with `make demand`
(`MULTIPLIER=1.3` adds a what-if scenario). Code:
`backend/app/engines/demand/*`, `backend/app/engines/supply_gap.py` (pure,
unit-tested), orchestrated by `backend/app/services/demand_run.py`.

> **Current inputs are not real enough to plan with.** Registrations are the
> synthetic VAHAN stand-in and 41 parameters use illustrative fallbacks, so
> every output carries confidence NONE. The UI says so above the numbers.
> Loading real VAHAN exports and filling `config/demand/*.yaml` changes the
> figures and upgrades their confidence; the method stays the same.

## 1. Registrations → vehicles in use

Plug-in registrations (BEV + PHEV; hybrids and fuel cells never plug in),
monthly, per RTO × segment. Stock at month t is each earlier registration
times `(1 − scrappage) ^ (age in years)`. Registrations before the first
observed month (Jan 2021 in the fixture) are unknown and excluded, which
under-counts early stock slightly.

## 2. Adoption forecast (Bass diffusion)

- Fit `N(t) = m·F(t)` (Bass) by least squares to monthly registrations, with
  market potential `m` bounded between just above what's already registered
  and 100× that. Three starting points guard against shallow local minima.
- **Stock in the forecast.** Bass projects *new adopters*. Scrapped EVs are
  assumed replaced by EVs, so stock grows by new adopters and never falls.
  (A first version fed Bass adoptions into the scrappage-only stock sum,
  and the fleet shrank after 2030 once adoption saturated. A unit test now
  guards against that.)
- **Cases:** `slow` ×0.7, `base` ×1.0, `fast` ×1.4 on new adopters after the
  last observed month; a what-if multiplier compounds on top. History is
  never altered.
- **Uncertainty:** 200 residual-bootstrap refits (seeded, so reruns match).
  P10/P50/P90 are taken on *stock*, per year-end.
- **Checks:** recovers known parameters from a synthetic Bass curve; beats a
  naive CAGR baseline on a 2-year holdout; quantiles are ordered; the seed
  reproduces the output exactly.
- Series with fewer than 30 registrations fall back to a flat trailing-mean
  projection and are labelled `flat_trailing_mean`.

## 3. Allocation to H3 cells (dasymetric)

A cell's share of an RTO's stock for a segment is the weighted average of its
share of each feature across that RTO's catchment
(`config/demand/allocation.yaml`):

| Segment | Weights |
|---|---|
| e-2W | population 0.8, commerce 0.2 |
| e-3W passenger | population 0.5, transit 0.3, commerce 0.2 |
| e-3W goods | population 0.3, commerce 0.4, industrial 0.3 |
| e-4W private | population 0.7, commerce 0.3 |
| e-4W fleet/taxi | population 0.4, transit 0.3, commerce 0.3 |
| e-bus | transit 0.7, population 0.3 |
| e-LCV/truck | industrial 0.5, commerce 0.3, population 0.2 |

Population comes from WorldPop (see `population-h3.md`); commerce, transit and
industrial are OSM POI counts per cell. Built-up share, night lights and
income aren't ingested yet, so they're absent rather than faked. Shares sum
to exactly 1, so **RTO totals are preserved**. A property test checks this
within 0.5%, and on the live database the worst segment error is 0.03% (from
per-cell rounding). The weights are judgement calls, so confidence is MEDIUM
at best.

**RTO catchments are approximate.** Real RTO jurisdictions don't follow OSM
taluka lines (Pimpri-Chinchwad sits inside Haveli taluka), so both PMR RTOs
(MH12, MH14) are spread over the whole Pune district until jurisdiction
boundaries are sourced.

## 4. Public charging demand

`public kWh/day = vehicles × annual km / 365 × kWh/km × public share`, per
segment, then summed. Sessions = kWh ÷ kWh per public session. The brief's
home-access modifier is wired in but held at 1 until a housing-type proxy
(apartment density, OSM building types) exists.

## 5. Existing supply and the gap (2SFCA)

- Station supply = Σ charge points × kW × availability × target utilisation
  × 24 h. OSM rarely tags power, so most charge points use an assumed kW, and
  stations listing none count as 1 charge point. The run records both counts.
- Catchment = 10-minute Valhalla drive-time isochrone; a cell is covered if
  its centroid falls inside.
- **Two-step floating catchment area.** Read literally, the brief's per-cell
  sum would count a station's full capacity once for *every* cell it covers.
  Instead each station's supply is shared across its catchment in proportion
  to demand (R_j = S_j / Σ D), and each cell's access ratio is the sum of R
  over the stations covering it. Gap = D × max(0, 1 − access).
- Additional charge points needed = ⌈gap ÷ (new charge point kW ×
  availability × utilisation × 24)⌉.
- Existing supply is held constant over the forecast. Announced or
  under-construction stations aren't modelled.

## 6. Areas (Area Planner)

An area is an OSM admin boundary; its cells are those whose centroid lies
inside. Quantiles are summed across cells: exact within one RTO × segment,
and conservatively wide across them (it assumes they move together). The API
reports **coverage**, meaning how much of the boundary lies inside the
modelled box; Mawal, for example, is only 33% covered, and the UI warns
about this. Ward-level areas wait on PMC/Census boundaries.

## 7. Provenance

Each run writes an evidence row holding its full configuration (weights,
catchments, cases, seed, bootstrap count, observation window, placeholders
in use, sourced parameters) plus input snapshot IDs, per-series Bass
parameters and fit error, and region totals per scenario and year. Outputs
built on synthetic registrations are confidence NONE; with real data, stock
is MEDIUM and demand/gap LOW until the placeholders are replaced.

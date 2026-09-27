# Candidate sites and feasibility screening (Phase 4)

Implements Sections 9.4 and 9.5 of the brief. A planning run is started from
the **Site planning** page (`/plans`) or `POST /api/v1/scenarios` followed by
`POST /api/v1/runs`, and executed by the Celery task `run.plan`. Code:
`backend/app/engines/candidates.py` and `backend/app/engines/feasibility.py`
(pure, unit-tested), orchestrated by `backend/app/services/candidate_run.py`.
Configuration: `config/candidates.yaml`, `config/policy/rules.yaml`.

> **Every candidate is labelled "Candidate site — requires field
> verification".** The screen uses OpenStreetMap geometry. It cannot see
> ownership, lease terms, parking layout, or the real feeder capacity. It
> removes sites that are clearly unsuitable. It does not approve any.

## 1. Run steps

`resolve_data → candidates → feasibility → funnel`. Each step writes a
`{step, status, at, …}` entry to `planning_run.progress`, and
`GET /runs/{id}/events` streams those entries as server-sent events. The run
also records:

- its inputs: the scenario, the resolved thresholds, which ones are
  placeholders, and the demand run used;
- `snapshot_ids`;
- `model_versions`;
- the git SHA.

Later phases add their own steps (score, optimise, size, …) to the same run.

The demand scenario is the one matching the scenario's adoption case and
multiplier (e.g. `base`, `fastx1.3`). If `make demand` hasn't produced that
scenario for the target year, the run fails with a message saying so.

## 2. Candidate generation (Section 9.4)

Every candidate records an `origin` and, where relevant, the host POI it
sits on.

| Origin | How | Config |
|---|---|---|
| `poi` | OSM POIs that can host a charger: fuel stations, parking, malls, hotels, supermarkets, office parks, transit hubs (not roadside bus stops), and restaurants within 100 m of a motorway or trunk road (dhabas, not city restaurants; primary was dropped in Phase 5 because Pune tags city arterials primary) | `host_pois` |
| `corridor` | Motorway and trunk roads are merged into continuous lines in UTM 43N. Lines of at least 3 km get a point every 5 km. Each point snaps to the best host within 1 km in the order fuel station → highway restaurant → parking → hotel → mall; with no host in range it stays on the road as `ROADSIDE` | `corridors` |
| `gap_cell` | The top 10 % of H3 cells by unmet public-charging demand (`gap_kwh`, Phase 3) for the target year. Each cell centre snaps to the nearest host within 300 m, else the nearest point on a road within 300 m. Cells with neither are counted as `gap_cells_unsnappable` and dropped | `gap_cells` |
| `user` | Sites entered on the scenario (lat, lng, name), validated to lie inside the modelled region | scenario `user_sites` |

**Dedupe.** Candidates within 150 m of each other are merged. The survivor is
chosen by these tie-breaks, in order:

1. Origin priority: user, then POI, then gap cell, then corridor.
2. A hosted site beats a roadside one.
3. Input order.

The survivor keeps the other origins in `merged_origins`, so a user site that
coincides with a mall and a gap cell shows all three.

## 3. Feasibility rules (Section 9.5)

Each surviving candidate is measured once in PostGIS:

- restricted land cover it falls inside;
- land-use context;
- nearest road and its class;
- nearest arterial;
- nearest existing DC station;
- nearest substation and the confidence of the grid data.

Its H3 cell's access ratio comes from the Phase 3 gap model.
`evaluate()` then applies each rule. Every rule reports one of five
outcomes, together with its measured value and threshold:

- `pass`
- `fail`
- `not_applied`: the rule doesn't apply to this scenario or its data is too weak
- `disabled`
- `deferred`

| Rule | Rejects when | Threshold | Status |
|---|---|---|---|
| F01 | Site lies inside water, forest, military land, a protected area, or an aerodrome (OSM `landcover` table) | — | enabled |
| F02 | No drivable road (including service roads and living streets) within the access distance | 50 m (from the brief) | enabled |
| F03 | DC scenarios only: an existing DC station is within the spacing distance **and** the cell is already served (access ratio ≥ threshold) | 1,000 m, ratio 1.0, **illustrative** | enabled |
| F04 | Nearest substation is beyond the distance limit, **only if grid data confidence is at least MEDIUM**. Weaker grid data is reported as `not_applied` and becomes a score penalty in Phase 5 (brief Section 2, principle 5) | 5,000 m, **illustrative** | enabled; never fires with today's OSM-only grid data (LOW) |
| F05 | DC scenarios only: site is on residential land use or a residential/unclassified street **and** the nearest arterial is beyond the distance limit | 300 m, **illustrative** | enabled |
| F06 | Hazard overlay | — | disabled: no flood layer ingested |
| F07 | Duplicate of a *selected* site | — | deferred to optimisation (Phase 6) |

**Arterial roads for F05** are motorway, trunk, primary, secondary and
tertiary, plus their `_link` roads. Tertiary was added after reviewing the
first runs, where F05 rejected 193 sites. Many of those were offices and
supermarkets on real through-roads that Pune's OSM tags `tertiary`, and a DC
charger there is reachable. With tertiary included, F05 rejects 78, mostly
gap-cell points and hosts deep inside residential colonies.

Thresholds follow the `value / illustrative` convention. Until a sourced
value is filled in, the illustrative fallback is used, the run lists it under
`placeholders_in_use`, and the run page shows it.

## 4. Evidence

Every rejection writes one `evidence` row per failing rule, with:

- metric `feasibility:<code>`;
- the measured value;
- the full rule detail as JSON;
- the method `candidates_feasibility_v1`;
- the snapshots the rule actually read. F01, F02, F04 and F05 cite only the
  OSM import. F03 also cites the demand and station inputs.

Confidence is:

- MEDIUM for OSM-geometry rules;
- LOW for F04;
- NONE for F03 while demand is synthetic.

Per-site measurements, including the rules that passed, are in
`site_feature`, and the pass/fail per rule is in `feasibility_result`.

API: `GET /runs/{id}/sites` (GeoJSON), `/runs/{id}/sites/{site_id}` (all rules
plus evidence ids), `/runs/{id}/rejected`, `/runs/{id}/funnel`.

## 5. Funnel

Logged per run (`planning_run.funnel`), shown on the run page:

| Stage | Meaning |
|---|---|
| theoretical | Every candidate before dedupe, by origin. Also records corridor samples and gap cells considered or unsnappable |
| candidates | After the 150 m dedupe, by origin, with the count merged |
| feasible / rejected | After the rules. Rejections are counted by reason code (a site failing two rules counts under both) |
| shortlisted | Top 3 × N by score (Phase 5, see [scoring.md](scoring.md)) |
| selected | Phase 6 (optimisation). `null` until then |

Pune, DC 60 + 120 kW, 2030 base, one user site (25 Sep 2026, before the
Phase 5 highway-restaurant change; see [scoring.md](scoring.md) for the
current figures):

- theoretical 2,730
- candidates 1,306
- feasible 1,175
- rejected 131: F05 78, F01 36, F02 25

F01 hits are:

- cantonments (Kirkee, Ghorpadi, College of Military Engineering);
- the Katraj Ghat and hill forests;
- one lake.

## 6. Known limits

- OSM land use and land cover are incomplete. A site on unmapped private or
  protected land passes F01.
- F03 and F05 thresholds are illustrative. Fill `config/policy/rules.yaml`
  from the Ministry of Power guidelines and the Maharashtra EV policy
  (`docs/verification.md`, item 9).
- F04 can't reject anything until a MEDIUM-confidence grid source (e.g.
  MSEDCL substations) is ingested.
- F03 depends on synthetic demand today, so its confidence is NONE.
- Write endpoints (`POST /scenarios`, `/runs`) accept anonymous requests.
  They record the owner when a user is signed in, but nothing enforces
  ownership until role-based access control is added.

# Scoring and the Site Explorer (Phase 5)

Implements Section 9.6 of the brief and module B, the Site Explorer.

- **Engine:** `backend/app/engines/scoring.py` (pure, unit-tested).
- **Orchestration:** `backend/app/services/scoring_run.py`, which runs as the
  `catchments` and `scoring` steps of every planning run, after feasibility.
- **Config:** `config/scoring.yaml` and the weight profiles in `config/weights/`.

> **Scores rank alternatives, they don't measure quality.** Each sub-score is
> a percentile among the run's feasible candidates. A 90 means "better than
> 90 % of the other candidates on this factor", not "90 % good". Today four
> of the eight sub-scores rest on synthetic demand (confidence NONE), and
> three are proxies (confidence LOW). The UI shows each sub-score's
> confidence, and the total takes the lowest confidence among its weighted
> inputs.

## 1. Catchments

Each feasible candidate gets a Valhalla **drive-time isochrone**. It uses the
same minutes as the Phase 3 supply model: `supply.catchment_minutes`, which
is 10.

- Requests run 8 at a time. The Pune run (1,099 sites) takes about 30 s.
- The polygon and the H3 res-8 cells whose centres fall inside it are stored
  in `site_catchment`. The site's own cell is always included.
- Phase 6 reuses these cells as the demand cells a site can serve.
- A site whose isochrone fails or comes back empty (off the routable network)
  is kept as `unscored` with the reason, and left out of the ranking.

## 2. Sub-scores

Each sub-score is scaled to 0–100 by average-rank percentile, so tied values
share a score.

| Sub-score | Raw measurement | Confidence today | Notes |
|---|---|---|---|
| demand | Σ P50 public kWh/day over catchment cells, target year | NONE (synthetic registrations) | From Phase 3 |
| accessibility | Mean of two percentiles: nearest road class (config ranking motorway → service) and catchment population (WorldPop) | LOW | The brief also names junction proximity. That needs a routable node table and isn't implemented |
| traffic | Metres of motorway/trunk/primary road (and their links) within 1 km | LOW | **Proxy.** No traffic counts or speed data are ingested. Replace with AADT or probe data when available |
| grid | Distance to the nearest OSM substation (lower is better) | LOW | Section 9.11 MVP proxy: proximity only, says nothing about capacity. F04 couldn't use it for rejection, so it counts here instead |
| land | Host suitability (0–1 per host type), plus a bonus when OSM parking is within 100 m | LOW | Suitability values are **illustrative** (`config/scoring.yaml`). No survey exists |
| future_growth | CAGR of catchment demand from the target year to target + 4 (capped at 2035; looks back instead when the target is 2035) | NONE | |
| competition_gap | 1 − min(1, existing supply ÷ demand) in the catchment. Existing supply uses the Phase 3 station-supply assumptions | NONE | |
| equity | Population share of the catchment in cells whose access ratio is below 0.5 (illustrative) | NONE | **Access only.** No income data is ingested, so the income part of the brief's definition is missing |

**Uninformative sub-scores.** If a sub-score's inputs are identical for every
candidate, every site gets 50 and the run records it under
`scoring.uninformative`. The UI then warns that the sub-score can't change the
order. In the Pune run this happens to **equity**: every catchment's access
ratio is below 0.5, because 37 stations serve the whole region. Equity
starts to discriminate once real supply data or higher adoption produces
served areas.

## 3. Total, profiles, rank

`total = Σ w_k · s_k`. The weights come from a profile in `config/weights/`
(the four presets in Section 9.6), and each scenario chooses its profile
(default `cpo_commercial`).

Sub-scores don't depend on the profile, so
`GET /runs/{id}/ranking?profile=…` re-ranks under another profile without
re-measuring anything. `weights=demand:0.5,grid:0.5` does the same with
custom weights, which are normalised to sum to 1. Ties rank by input order,
so reruns give the same result.

The **shortlist** is the top 3 × N, where N = 20 (`top_n`,
`shortlist_multiple`). That is the pool the Phase 6 why-not engine
re-examines. The funnel records it as `shortlisted`.

## 4. Rank robustness

The weights are perturbed 1,000 times with a Dirichlet distribution centred
on the profile:

- α = 100 · w.
- The coefficient of variation of a weight w is √((1 − w) / (w · 101)), about
  20 % at w = 0.2, which matches the brief's "±20 %".
- Zero weights stay zero: the profile chose to ignore those factors.
- The seed is fixed, so badges are reproducible.

The badge reads "Top 20 in 87 % of weight variations". In the Pune run, 10 of
the top 20 stay in the top 20 in at least 80 % of draws. The other ten sit
close enough to the cut-off that the ordering among them is a matter of
weighting.

## 5. Catchment indices (region = 100)

These are shown in the site drawer and the comparison table.

| Index | Catchment value | Benchmark |
|---|---|---|
| Population density | people per modelled cell | region average |
| EVs per 1,000 people | target year P50 | region |
| Charging demand density | public kWh/day per cell | region |
| Existing charge points per 1,000 EVs | charge points inside the isochrone | region |
| Income | **not available**: no source ingested | — |
| Home-charging access | **not available**: no housing-type source ingested | — |

The brief asks for the income and housing indices. They are shown as
unavailable, not estimated. "Traffic trend" and "P50 monthly revenue" from
the module B spec need traffic data and the Phase 7 finance engine, and the
comparison table says so.

## 6. Evidence and storage

- `run_site` holds one row per candidate per run: rank, total, sub-scores,
  robustness, and `detail`, which holds:
  - the raw measurements;
  - catchment totals;
  - the indices;
  - POI counts by category inside the catchment.

  Its `selected`, `coverage_kwh` and `why_not` columns are filled in by
  Phase 6.
- Every scored site gets an `evidence` row with:
  - metric `score:total`;
  - method `scoring_v1`;
  - the run's snapshots;
  - confidence equal to the lowest confidence among its weighted sub-scores.
- `planning_run.inputs.scoring` records the profile, weights, config,
  benchmarks, confidence per sub-score, and uninformative flags.

## 7. Result: Pune, DC 60 + 120 kW, 2030 base, CPO commercial (25 Sep 2026)

- **Funnel:**

  | Stage | Sites |
  |---|---|
  | Theoretical | 2,282 |
  | Candidates | 1,229 |
  | Feasible and scored | 1,099 |
  | Shortlisted | 60 |

- **Rejected:** 130 (F05 78, F01 35, F02 25).
- **Time:** the whole run took 43 s.
- **Top 20 by host type:**
  - 8 fuel stations;
  - 6 transit hubs;
  - 3 parking;
  - 2 highway restaurants;
  - 1 mall.

  All sit in central Pune, where the synthetic demand allocation puts the
  most EVs.

This run also includes a Phase 4 config change. Highway-restaurant hosts now
require a motorway or trunk road within 100 m. Pune tags its city arterials
`primary`, so the old rule admitted 577 city restaurants as "highway" hosts;
the new one admits 129. The two ranked in this run's top 20 are both beside
trunk roads that pass through the city, so some urban restaurants still get
in.

## 8. Known limits

- Demand-driven sub-scores stay illustrative until real VAHAN data replaces
  the synthetic registrations.
- The traffic and grid scores are proxies. Swap in real sources, TomTom/HERE
  traffic and DISCOM feeder data (Phase 10), behind the same sub-score names.
- Accessibility doesn't include junction proximity.
- Equity has no income component.
- Percentiles are relative to each run's candidate set. The same site can
  score differently in runs with different candidates, so compare sites
  within a run, not across runs.

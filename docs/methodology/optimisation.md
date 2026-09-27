# Optimisation: plan, why-not, what-if, strategies (Phase 6)

Implements Section 9.7 of the brief, module C (Scenario Comparison) and
module D (Catchment Optimiser).

- **Engine:** `backend/app/engines/optimisation/mclp.py` (pure, unit-tested).
- **Orchestration:** `backend/app/services/optimisation_run.py`, which adds
  the `optimise`, `why_not` and `strategies` steps to each run whose
  scenario has a budget.
- **Config:** `config/optimisation.yaml` and `config/costs/unit_costs.yaml`.
- **Solver choices:** [ADR 0007](../adr/0007-optimisation-solver-and-pareto.md).

> **Costs are illustrative and demand is synthetic.** Unit costs are
> round-number stand-ins, not quotes, and registrations are the synthetic
> VAHAN fixture. The plan shows how the method works; don't read it as a
> recommendation. Every site in it is a candidate that still needs field
> verification.

## 1. Model A: budget-constrained capacitated maximal covering

| | |
|---|---|
| Demand cells *i* | H3 res-8 cells with **unmet** public demand `d_i` (the Phase 3 gap, kWh/day, P50, target year) |
| Sites *j* | The best 200 feasible candidates by score, plus every user site |
| Bundles *k* | *n* chargers of one scenario class, *n* ∈ {2, 4} (e.g. DC_120 × 4) |
| Serve | `y_ij` kWh/day, only where cell *i* is inside site *j*'s 10-minute drive catchment (Phase 5) |

The objective is to **maximise** `Σ v_j·y_ij + β·Σ p_i·c_i − λ·Σ cost_k·z_jk`, subject to these constraints:

- one bundle per open site: `Σ_k z_jk = x_j`;
- capacity: `Σ_i y_ij ≤ Σ_k cap_k·z_jk`;
- a cell's served demand can't exceed its unmet demand: `Σ_j y_ij ≤ d_i`;
- `Σ cost ≤ budget`;
- `Σ x_j ≤ N_max`;
- sites closer than 1.5 km (illustrative) can't both open;
- optionally, forced or excluded sites, and a minimum total served.

The terms:

- **Capacity.** `cap_k = kW × 24 h × availability × target utilisation`,
  using the same supply assumptions as the Phase 3 gap model, so the gap
  and the plan agree on what a charger delivers.
- **Value.**
  - The scenario plan values kWh by site score:
    `v_j = 0.5 + score_j / 200`, so a top-scored site's kWh counts double a
    bottom-scored one's.
  - `λ = 1e-6` per rupee is a tie-breaker that stops the model buying
    capacity it can't fill.
  - `β` (population coverage of underserved cells) is used only by the
    equity strategy.
- **Site cost** (`bundle()`):
  - chargers;
  - 10% installation;
  - civil works;
  - canopy;
  - software;
  - LT connection. If the sanctioned load (kW × 0.8 diversity) exceeds
    150 kW, an HT connection plus a transformer instead;
  - 10% contingency.

  Every line is an illustrative placeholder. Land lease is excluded until
  Phase 7 knows each site's footprint.

**Solving.** Cells covered by the same set of sites are merged into zones;
this is lossless. A greedy plan, evaluated exactly, then races the HiGHS MIP
(ADR 0007). The status is honest about which one won:

- `optimal`: proven within 1%;
- `feasible`: a bound exists, and the gap is shown;
- `heuristic`: no bound yet.

## 2. Why-not

The candidates left out of the plan that rank within the top 3 × N by score
(up to 15) are each re-solved with the site forced open. Each one records:

- the objective and served-kWh change;
- the cost change;
- which planned site it displaces;
- spacing conflicts;
- the **deciding sub-score**: the weighted sub-score on which it trails the
  site it would displace most.

Results are stored in `run_site.why_not` and shown in the Plan tab and the
site drawer. A typical explanation: *"Would replace MNGL CNG Pump; trails it
on Accessibility. Too close to MNGL CNG Pump."*

## 3. What-if (target < 10 s)

`POST /runs/{id}/whatif` accepts `budget_inr`, `max_sites` and
`adoption_multiplier`.

- It re-solves the scenario plan from the run's cached context: scores,
  catchments and cells stay in memory in the API process.
- An adoption multiplier swaps in that scenario's demand. It must exist
  (`make demand MULTIPLIER=1.3`); if it doesn't, the API says so.
- The response diffs the result against the base plan: sites added,
  removed, resized and kept, plus KPI deltas.
- The time limit is 6.5 s; `solver_stats.total_s` records the time end to
  end.

Scores are **not** recomputed for the new adoption level: sub-scores
describe sites, and the demand they serve comes from the new scenario.

## 4. Strategies and the Pareto front (module C)

| Strategy | Objective |
|---|---|
| Maximum coverage | Maximise kWh/day served |
| Equity focus | kWh/day at weight 0.25, plus population coverage of underserved cells (scaled so covering everyone equals the region's gap) |
| Cost optimised | The cheapest plan serving ≥ 90% of what maximum coverage serves at the same budget |
| Commercial return | kWh weighted by site score under the CPO-commercial profile |

- Each strategy is solved at 50/100/150/200% of the scenario budget. The
  100% points are the strategy cards; all 16 points form the Pareto chart,
  with points not dominated on coverage ↑, cost ↓ and equity ↑ flagged
  `on_front`.
- When strategies coincide, the UI says so and explains why.
- The brief's "Custom" strategy is covered by what-ifs and custom ranking
  weights.

## 5. Catchments (module D)

`GET /optimisations/{id}/cells?mode=` has two modes:

- **`assignment`:** the site serving the largest share of each cell's
  demand in the plan (from `y_ij`).
- **`voronoi`:** the site each cell reaches soonest by car.
  - Valhalla isochrones in 5/10/20/30-minute bands per selected site.
  - Each cell goes to its fastest band; ties go to the nearer site in a
    straight line.
  - Computed on first request and cached on the optimisation.

## 6. Result: headline demo (Pune, 2028, base adoption, DC 60/120 kW, ₹10 Cr, ≤ 20 sites; 26 Sep 2026)

- **Scenario plan:** proven optimal (HiGHS, 4.3 s).
  - 8 sites, 28 charge points (26 × DC 120 kW, 2 × DC 60 kW);
  - ₹9.95 Cr;
  - serves 11.4% of the region's unmet public demand (18,468 of
    162,262 kWh/day);
  - 36.7% of underserved people live inside a selected site's catchment;
  - utilisation 100%: demand exceeds what ₹10 Cr can build.
- **Not 20 sites.** The limit is an upper bound. With these illustrative
  costs, 4 × DC 120 kW bundles deliver the most kWh per rupee (an HT
  connection is shared across more capacity), so fewer, larger sites win.
  Different prices change this.
- **What-if: ₹20 Cr and adoption +30%.** Re-optimised in 6.6–7.6 s end to
  end:
  - 16 sites;
  - coverage 20.7%;
  - 8–9 sites added, 0–1 removed, 2 resized. The exact counts depend on
    whether the MIP or the greedy plan wins within the limit.
- **Strategies at ₹10 Cr:**

  | Strategy | Sites | Cost | Demand served | Underserved reached |
  |---|---|---|---|---|
  | Maximum coverage | 9 | ₹9.87 Cr | 11.4% | 38.2% |
  | Equity focus | 19 (mostly DC 60 kW) | ₹9.87 Cr | 10.5% | 52.0% |
  | Cost optimised | 7 | ₹9.04 Cr | 10.5% | 32.2% |

- **Full run:** 2.5–3.5 minutes, peak worker memory about 0.9 GB. Why-not and
  the 16-solve grid account for about 1.5 minutes of that.

## 7. Known limits

- Costs, spacing and the LT/HT threshold are illustrative
  (`config/costs/unit_costs.yaml`, `config/optimisation.yaml`).
- Capacity uses a flat utilisation target. Queueing-based sizing
  (Erlang-C, SimPy) arrives in Phase 7 and will replace `cap_k`.
- **Model C** (multi-period phasing) is not implemented. Every plan opens
  in the target year.
- Strategy and Pareto solves have short time limits. Some end as
  `heuristic` (greedy plan, no proven gap) and are labelled as such.
- What-if contexts are cached per API process for the most recent run
  only.

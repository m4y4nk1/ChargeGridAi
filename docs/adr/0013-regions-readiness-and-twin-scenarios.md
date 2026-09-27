# 0013: Region registry with readiness scores, and region-wide twin scenarios

## Status
Accepted (Phase 12)

## Context
The brief's scale-out phase has three parts:

- expand to the Mumbai–Pune corridor, Maharashtra and the top metros;
- run national digital-twin questions: "add 10,000 fast chargers", "adoption +30%",
  "where does the grid constrain?";
- acceptance: a region onboarding playbook and per-region data readiness scores.

Two constraints apply:

- **Local capacity.** One laptop (4 GB for Docker) can't hold state-scale routing
  graphs and demand grids.
- **No invented inputs.** Registrations, RTO lists and tariffs for new regions must
  not be made up.

## Decision
1. **Planned regions in the registry.** Maharashtra and six metros are registered in
   `config/regions.yaml` with `status: planned`, approximate bounding boxes and empty
   RTO lists (to be sourced).
   - They're scored for readiness.
   - Scenarios, runs and twin scenarios refuse them until they're flipped to
     `onboarded`.
2. **Readiness scores** come from checks against the platform's own data, weighted in
   `config/readiness.yaml`:
   - OSM density, boundaries and population cells;
   - real vs synthetic registrations;
   - demand runs;
   - fused charger sources;
   - DISCOM vs OSM-only grid;
   - solar;
   - sourced vs placeholder tariffs and costs;
   - operator feeds;
   - whether Valhalla snaps the region centre.

   Each dimension carries its fix command. Roads, routing, population or demand at zero
   blocks planning.
3. **Onboarding** is a script (`app/ingestion/onboard.py`, `make onboard-region`) plus
   a playbook (docs/onboarding-regions.md).
   - The script runs the idempotent data steps.
   - The routing rebuild and the status flip stay manual: one needs a restart, the
     other a review.
4. **Twin scenarios run on the demand grid**, not on candidate sites, so they cover
   whole regions in seconds.
   - Chargers are allocated greedily to the largest remaining gap. Each charger
     delivers the same kWh/day the gap model assumes.
   - Overlapping regions are merged per cell, with the smallest region winning.
   - Grid load is totalled per nearest substation, as sanctioned load (chargers × kW ×
     diversity). Rated substations are assessed with the same headroom engine as Grid
     Impact.
   - Results are stored (`twin_run`), audited, available to the assistant (`twin.run`)
     and labelled screening estimates, with demand confidence NONE while registrations
     are synthetic.

## Consequences
- **"National" means onboarded regions only.** The results say which regions they
  cover.
- **Twin placements aren't site plans.** They answer where the most unserved demand is.
  A planning run is still needed for sites.
- **Maharashtra and the metros stay planned.** Onboarding them needs sourced RTO lists,
  registrations, and the capacity for their routing graphs.

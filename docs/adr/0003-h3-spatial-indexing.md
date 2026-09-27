# 0003: H3 as the spatial indexing scheme

## Status
Accepted

## Context
Demand, supply, and gap calculations need a consistent way to aggregate
point/polygon data (population rasters, EV registrations, POIs, charging
stations) into comparable cells across the whole region, at a resolution
fine enough for urban site selection but coarse enough to be tractable for
optimisation (thousands, not millions, of cells per run).

## Decision
Use Uber's H3 hexagonal grid as the primary spatial index:
- **Resolution 8** (~0.74 km² per cell) for urban demand/supply/gap analysis
  within Pune Metropolitan Region.
- **Resolution 7** for highway corridors and state-level views, combined with
  road-segment linear referencing for along-corridor positioning.

`h3-pg` runs inside PostgreSQL (alongside PostGIS) so cell assignment and
aggregation can happen in SQL, and `h3-py` is used for the same operations in
the Python engines, keeping one indexing scheme end-to-end from ingestion to
the `H3HexagonLayer` on the frontend map.

## Consequences
- All `h3_cell_feature` rows are keyed by resolution, so any query or engine
  touching demand/supply data must know which resolution it's operating at;
  mixing res-7 and res-8 values without reprojecting through a proper H3
  parent/child lookup produces wrong aggregates.
- The custom Postgres image (ADR 0001) must build h3-pg from source, since no
  official PostGIS image bundles it — this is a one-time infra cost paid in
  Phase 0.
- Choosing hexagons over a square/administrative-boundary grid gives more
  uniform neighbor distances for isochrone/catchment approximations, at the
  cost of H3 being a less familiar tool than plain lat/lng binning for anyone
  extending the codebase later.

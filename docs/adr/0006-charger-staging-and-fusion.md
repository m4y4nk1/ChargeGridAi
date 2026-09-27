# 0006: Stage charger records per source, then rebuild the fused store

## Status
Accepted

## Context
Charging stations arrive from several sources on different cadences (OSM
daily, OCM daily, government registry monthly by hand). Section 7.3
requires one fused `charging_station` entity with per-source provenance,
recorded conflicts, and a strict separation of stations, EVSEs and
connectors. Updating the fused tables in place, source by source, would
make results depend on the order sources were ingested.

## Decision
- Each source's ingestion normalises its records and replaces only that
  source's rows in `charger_source_record`, writing one `dataset_snapshot`.
- Fusion is a pure function over *all* staged records. The fusion job
  deletes and rebuilds `charging_station` / `evse` / `connector` /
  `station_source_link` in one transaction.
- Fused station IDs are `uuid5` of the anchor record (the highest-priority
  member), so IDs are stable across rebuilds while that record exists.

## Consequences
- Ingesting sources in any order converges to the same fused result.
- A station's ID changes if its anchor record disappears or a
  higher-priority source starts covering it. Anything that must outlive
  that (for example, planning runs in Phase 6) has to copy the station
  attributes it depends on into run-scoped evidence, not just reference
  the ID.
- A full rebuild is cheap at PMR scale (tens to low thousands of stations).
  At national scale this should become incremental per H3 block, which is
  already the unit fusion compares within.

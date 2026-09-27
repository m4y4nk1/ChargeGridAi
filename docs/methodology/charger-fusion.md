# Charging-station fusion (Phase 2)

Implements Section 7.3 of the brief. Code: `backend/app/ingestion/fusion.py`
(pure, unit-tested in `tests/unit/test_fusion.py`).

## Inputs

Each source's ingestion job writes its records, normalised to one schema
(`providers/base.py::ChargingStation`), into `charger_source_record`. That
table is replaced per source on every run. Fusion then rebuilds
`charging_station` / `evse` / `connector` / `station_source_link` from
everything staged.

| Source | Licence class | Status (Sep 2026) |
|---|---|---|
| OSM `amenity=charging_station` | OPEN | Live — from the osm2pgsql import |
| Open Charge Map | OPEN (non-open DataProvider records dropped) | Adapter built; needs a free `OCM_API_KEY` |
| BEE EV Yatra / BHEL | GOVERNMENT | CSV importer built; no export or API available |
| Google Places | RESTRICTED_GOOGLE | Never enters the fused store; `fuse()` refuses it |

## Matching

1. **Blocking.** Records are only compared if they fall within a 3-ring
   disk of each other's H3 res-10 cell (about 200 m).
2. **Score** = 0.6 × distance + 0.25 × name/operator + 0.15 × power:
   - distance: 1.0 up to 75 m, then 0.8 → 0.3 across 75–200 m, no match
     beyond 200 m;
   - name/operator: pg_trgm-compatible trigram similarity, taking the better
     of name and operator; 0.5 if neither side has one;
   - power: agreement of max kW (within 25%) and Jaccard overlap of
     connector standards; 0.5 if unknown.
3. **Vetoes.** Two records from the same source never merge. Two *known*
   operators with trigram similarity below 0.3 never merge — two CPOs can
   share a mall car park.
4. Pairs scoring ≥ 0.6 are merged best-first (union-find), refusing any
   merge that would put two records from one source in the same station.

## Merging

- Field precedence: government > operator feed > OCM > OSM.
- EVSEs are taken whole from the highest-precedence source that lists any;
  they are never summed across sources, because that would double-count.
- Licence class of the fused station is its most restrictive member's.
- Confidence: HIGH for a government anchor or two or more agreeing
  sources; MEDIUM for one open source; LOW if the only source is LOW.
- The station ID is `uuid5(source:record_id)` of the anchor record, so it's
  stable across re-runs while that record exists.

## Conflicts

Recorded on the station (`charging_station.conflicts`) and shown in the
evidence drawer rather than silently resolved: differing charge-point
totals, max kW differing by more than 25%, and operator names that don't
agree (trigram < 0.6).

## Stations vs charge points vs connectors

Sources describe capacity differently (`providers/charger_normalize.py`).
When a source gives a charge-point count and all sockets share one current
type, that count wins; otherwise connector quantities stand in for charge
points per current type. OSM stations with no socket tags get **no EVSEs**
rather than an invented default. The stats endpoint states this caveat
alongside its numbers.

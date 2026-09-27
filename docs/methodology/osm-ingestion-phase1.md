# Phase 1 OSM fixture: how the Pune extract was built

This is a note on the ad hoc extract used to bring up the Phase 1 foundation
(`road_segment`, `poi`, `grid_asset`, `admin_boundary`), not a description of
the production `ingest_osm` job (Section 13), which will pull from Geofabrik
+ pyosmium diffs per `docs/verification.md` item 7 once Phase 2 builds it
out. Recorded here because the gotchas are easy to hit again.

## What was fetched

No Maharashtra-specific Geofabrik extract exists (verification.md item 7),
and downloading the full India PBF (~1.6 GB) for a Pune-only Phase 1 fixture
was more than needed. Instead, two Overpass API queries were combined:

1. `GET https://overpass-api.de/api/map?bbox=73.70,18.40,73.98,18.65` — full
   data (nodes/ways/relations, all tags) for the Pune urban core. Overpass's
   `/api/map` endpoint refuses boxes above ~0.25 square degrees, so this had
   to be the tight core box, not the full metro region.
2. A second, much smaller Overpass QL query for the *administrative boundary
   relations* specifically (`relation["boundary"="administrative"](south,
   west,north,east)`), covering the wider PMR box, since those relations
   extend well beyond the core box in query 1.

## Gotchas hit

- **Overpass QL bbox order is `(south,west,north,east)`** — i.e.
  `(min_lat,min_lon,max_lat,max_lon)` — which is the reverse of the
  `/api/map?bbox=` REST parameter (`min_lon,min_lat,max_lon,max_lat`). Mixing
  these up silently returns almost nothing, not an error.
- **`/api/map` truncates large responses mid-stream** with a `<remark>`
  element and no error status. The node/way sections came through complete;
  most administrative boundary *relations* were cut off, since relations are
  emitted last and are large (a district boundary can have 300+ member
  ways). `osm2pgsql`'s `object:as_multipolygon()` silently returns nothing
  for a relation with incomplete members — check row counts after import,
  don't assume a clean `osm2pgsql` exit means every relation resolved.
- **A two-statement Overpass query (`out body; >; out skel qt;`) emits
  relations before their recursed-in nodes/ways**, which violates the
  strict node-then-way-then-relation ordering `osm2pgsql`/`osmium` require.
  Use one combined statement — `(._;>;); out meta;` — so Overpass's normal
  per-type-sorted output order holds.
- **`osmium merge` needs matching version metadata to deduplicate
  overlapping extracts.** Nodes fetched with `out skel` (no metadata) won't
  merge cleanly against the same nodes fetched with `out meta` elsewhere —
  same ID, different apparent "version" (missing vs present), so `osm2pgsql`
  sees a duplicate. Fetch with `out meta`/`out body` whenever the result
  will be merged with another extract; sort each file with `osmium sort`
  before merging.
- To backfill just a handful of specific broken relations, `relation(id:
  ...);(._;>;);out meta;` (an ID lookup, not a bbox scan) is far cheaper and
  less likely to hit Overpass's public-server rate limiting than re-running
  the full bbox query.

## Known gap

OSM has no ward-level (Pune Municipal Corporation) boundaries in this area —
only state/district/taluka (`admin_level` 4–7). `admin_boundary.level` will
show `'ward'` only once Census/PMC ward boundary data is ingested in Phase 2
(Section 7.1 row 6); don't assume `poi`/`road_segment` gaps mean the same
thing, those are dense and complete for this extract.

## Phase 4 additions

- **`landcover`** (new table, areas only) is used by feasibility rules F01
  and F05:
  - *Restricted* classes, where a candidate is rejected: water, forest,
    military, protected area, aerodrome.
  - *Context* classes, where land use is recorded but nothing is rejected:
    residential, commercial, industrial.
  - Closed ways and multipolygon relations are both imported. The Pune
    import has 2,545 polygons.
- **`road_segment`** now includes `service` and `living_street` roads.
  Without them, F02 (road access within 50 m) rejected 181 sites, mostly
  malls and office parks reached by internal service roads. With them, it
  rejects 25. The import has 73,877 segments.

Re-run `make ingest-osm` after pulling these changes so both tables exist.

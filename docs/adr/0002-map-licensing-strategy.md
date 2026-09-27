# 0002: Map rendering and data-licensing strategy

## Status
Accepted

## Context
ChargeGrid AI mixes data under materially different licences on the same
map: OpenStreetMap (ODbL), government/open datasets, Open Charge Map
(per-record provider licence), and commercial providers (Google, Mappls,
TomTom) whose terms restrict caching and cross-provider display. Rendering
Google-sourced content (e.g. Places results) on a non-Google basemap is a
terms violation for most of these providers; conversely, an entirely
Google-based UI would make it hard to publish an open-data export or run in
a context without a paid Google key. This has to be solved once, at the
architecture level, not per-feature.

## Decision
Run two map surfaces behind one map abstraction:
1. **Google Maps JS API + deck.gl `GoogleMapsOverlay`** — the required
   canvas whenever Google-derived content (Places results, Solar API
   overlays, Google-attributed imagery) is shown.
2. **MapLibre GL JS + an OSM-based basemap** (self-hosted PMTiles/OpenFreeMap)
   — the "open-data mode" used for exports and any view that only shows
   OSM/government/synthetic data.

Every feature/layer object carries a `license_class`
(`OPEN | GOVERNMENT | RESTRICTED_GOOGLE | RESTRICTED_COMMERCIAL | SYNTHETIC`)
from ingestion through the API to the frontend. A single guard module
(`frontend/src/map/licenseGuard.ts`, to be implemented in Phase 1) refuses to
render `RESTRICTED_GOOGLE`/`RESTRICTED_COMMERCIAL` features on the MapLibre
canvas and injects the required attribution strings per visible layer.
Exports (GeoJSON/XLSX/PDF) strip restricted fields and append an attribution
sheet.

## Consequences
- Every provider adapter must tag its output with `license_class` — this is
  already encoded in `backend/app/providers/base.py`'s `ProvenancedModel`.
- Any new map layer added later must declare which canvas(es) it's allowed
  on; this is a code-review checklist item, not just documentation.
- The two-canvas approach costs extra frontend surface area (two map
  components, shared layer definitions) in exchange for never having to
  relitigate licensing per feature.
- Current Google Maps Platform terms, pricing, and Mappls terms are tracked
  in `docs/verification.md` and must be re-checked before each release per
  Section 14 of the brief — this ADR records the strategy, not a point-in-time
  reading of the terms.

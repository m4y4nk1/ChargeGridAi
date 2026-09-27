# Gridded population → H3 (Phase 2)

Code: `backend/app/providers/rasters/worldpop.py`, loaded by
`ingestion/pipelines.py::ingest_population` (`make ingest-population`).

## Source

WorldPop Global2 **R2025A**, building-footprint-constrained, CC BY 4.0.
Years 2015–2030 are published; Phase 2 loads **2025**. Two grids:

| Grid | File | Size (India) | Notes |
|---|---|---|---|
| 100 m | `ind_pop_2025_CN_100m_R2025A_v1.tif` | ~0.8 GB | Preferred. Not UN-adjusted. |
| 1 km | `ind_pop_2025_CN_1km_R2025A_UA_v1.tif` | ~16 MB | Fallback. UN-adjusted, so totals differ slightly from 100 m. |

**What's loaded now: the 1 km grid** (7.77 M people in the PMR box, 5,201
res-8 cells, confidence LOW). WorldPop's server ignores HTTP Range requests,
so a dropped connection can't be resumed. At the ~300 KB/s it served during
Phase 2, the 100 m download failed partway (a timeout at 190 MB).
`make ingest-population GRID=100m` replaces the 1 km data whenever that
download succeeds.

## Method

- The national raster is downloaded, cropped to the PMR box
  (73.55–74.15 E, 18.30–18.80 N), and the national file is deleted. Only the
  small cropped GeoTIFF is kept; it's also the snapshot's stored raw file.
- **100 m:** each pixel's people go to the H3 res-8 cell containing the
  pixel centre (~70 pixels per cell).
- **1 km:** a pixel is about the size of one res-8 cell, so centre
  assignment would be lumpy. Each pixel is split into 10 × 10 equal
  sub-points (simple areal interpolation) before assignment, which spreads
  its people across the cells it overlaps. This assumes population is
  uniform within the 1 km pixel, which is why confidence is LOW.
- Both preserve the regional total exactly. A property test covers this,
  and every snapshot's quality report records `pixel_total`, `cell_total`
  and `total_preserved`.
- Re-running for a year replaces that region/year's cells rather than
  merging, so switching grids never leaves stale cells behind.

## Output

`h3_cell` (geometry) and `h3_cell_feature` (`year`, `scenario='observed'`,
`population`), plus a `population_2025` evidence row for the region.

## Not yet done (Phase 3)

Calibration to Census/ward totals, and dasymetric weights for EV-stock
allocation.

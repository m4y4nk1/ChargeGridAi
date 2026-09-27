"""WorldPop gridded population -> H3 (Section 7.1 row 7; Section 9.1).

WorldPop's server ignores HTTP Range requests, so the national raster must
be downloaded whole; `crop_to_bbox` cuts the region window out so the
~1.8 GB national file can be deleted straight after. Each pixel's people
are assigned to the H3 cell containing the pixel centre, so the regional
total is preserved exactly (checked in the quality report and tests).

Licence: CC BY 4.0 (https://hub.worldpop.org/data/licence.txt).
"""

from dataclasses import dataclass
from pathlib import Path

import h3
import numpy as np
import rasterio
from rasterio.windows import from_bounds

from app.providers.base import BBox

# WorldPop Global2 R2025A, building-footprint-constrained, 2015-2030.
# The 100 m layer is the one to use; the 1 km layer (UN-adjusted) is a
# fallback for when the ~0.8 GB 100 m download can't complete.
WORLDPOP_YEARS = range(2015, 2031)
GRIDS = ("100m", "1km")


def worldpop_ind_filename(year: int, grid: str = "100m") -> str:
    if grid == "100m":
        return f"ind_pop_{year}_CN_100m_R2025A_v1.tif"
    if grid == "1km":
        return f"ind_pop_{year}_CN_1km_R2025A_UA_v1.tif"
    raise ValueError(f"grid must be one of {GRIDS}")


def worldpop_ind_url(year: int, grid: str = "100m") -> str:
    if year not in WORLDPOP_YEARS:
        raise ValueError(f"WorldPop R2025A covers {WORLDPOP_YEARS.start}-{WORLDPOP_YEARS.stop - 1}")
    folder = "100m" if grid == "100m" else "1km_ua"
    return (
        "https://data.worldpop.org/GIS/Population/Global_2015_2030/R2025A/"
        f"{year}/IND/v1/{folder}/constrained/{worldpop_ind_filename(year, grid)}"
    )


@dataclass(frozen=True)
class H3Population:
    cells: dict[str, float]
    pixel_total: float
    nodata_share: float
    pixel_count: int


def crop_to_bbox(src_path: Path, dst_path: Path, bbox: BBox) -> Path:
    with rasterio.open(src_path) as src:
        window = (
            from_bounds(bbox.min_lng, bbox.min_lat, bbox.max_lng, bbox.max_lat, src.transform)
            .round_offsets()
            .round_lengths()
        )
        data = src.read(1, window=window)
        profile = src.profile | {
            "height": data.shape[0],
            "width": data.shape[1],
            "transform": src.window_transform(window),
            "compress": "deflate",
            "tiled": False,
        }
        profile.pop("blockxsize", None)
        profile.pop("blockysize", None)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(dst_path, "w", **profile) as dst:
        dst.write(data, 1)
    return dst_path


def aggregate_to_h3(raster_path: Path, res: int, subsample: int = 1) -> H3Population:
    """Assign each pixel's population to H3 cells, preserving the total exactly.

    subsample=1 uses the pixel centre (right for 100 m pixels, ~70 per res-8
    cell). For coarse pixels (1 km, about one res-8 cell each), subsample=n
    splits every pixel into n x n equal sub-points (simple areal
    interpolation) so its people spread across the cells it overlaps.
    """
    with rasterio.open(raster_path) as src:
        data = src.read(1)
        nodata = src.nodata
        transform = src.transform

    valid = np.isfinite(data) & (data > 0)
    if nodata is not None:
        valid &= data != nodata
    rows, cols = np.nonzero(valid)
    values = data[rows, cols].astype(float)

    offsets = (np.arange(subsample) + 0.5) / subsample
    cells: dict[str, float] = {}
    for dr in offsets:
        for dc in offsets:
            xs, ys = transform @ (cols + dc, rows + dr)
            for lat, lng, value in zip(ys, xs, values, strict=True):
                cell = h3.latlng_to_cell(float(lat), float(lng), res)
                cells[cell] = cells.get(cell, 0.0) + float(value) / subsample**2

    nodata_pixels = int(np.count_nonzero(data == nodata)) if nodata is not None else 0
    return H3Population(
        cells=cells,
        pixel_total=float(values.sum()),
        nodata_share=nodata_pixels / data.size,
        pixel_count=int(data.size),
    )

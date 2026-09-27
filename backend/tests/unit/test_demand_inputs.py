import csv
from pathlib import Path

import numpy as np
import pytest
import rasterio
from hypothesis import given, settings
from hypothesis import strategies as st
from rasterio.transform import from_origin

from app.core.settings import get_settings
from app.ingestion.synthetic import SYNTHETIC_RTOS, generate_synthetic_vahan_csv
from app.providers.government.vahan import VahanImportError, VahanMappings, load_vahan_csv
from app.providers.rasters.worldpop import aggregate_to_h3

MAPPINGS = VahanMappings.load(get_settings().config_dir / "demand" / "segments.yaml")


def _write(path: Path, rows: list[list[str]]) -> Path:
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["rto_code", "month", "fuel", "vehicle_class", "count"])
        writer.writerows(rows)
    return path


def test_vahan_rows_map_to_segments_and_totals_are_checked(tmp_path: Path) -> None:
    rows = load_vahan_csv(
        _write(
            tmp_path / "v.csv",
            [
                ["mh12", "2025-07", "ELECTRIC(BOV)", "M-Cycle/Scooter", "1,200"],
                ["MH12", "2025-07", "ELECTRIC(BOV)", "Motor Car", "300"],
                ["MH12", "2025-07", "ELECTRIC(BOV)", "TOTAL", "1500"],
            ],
        ),
        MAPPINGS,
    )
    assert {(r.segment, r.count, r.ev_type) for r in rows} == {
        ("e2w", 1200, "BEV"),
        ("e4w_private", 300, "BEV"),
    }


def test_vahan_total_mismatch_fails(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "v.csv",
        [
            ["MH12", "2025-07", "ELECTRIC(BOV)", "Motor Car", "300"],
            ["MH12", "2025-07", "ELECTRIC(BOV)", "TOTAL", "301"],
        ],
    )
    with pytest.raises(VahanImportError, match="TOTAL"):
        load_vahan_csv(path, MAPPINGS)


def test_vahan_unmapped_class_fails_rather_than_guessing(tmp_path: Path) -> None:
    path = _write(tmp_path / "v.csv", [["MH12", "2025-07", "ELECTRIC(BOV)", "Tractor", "5"]])
    with pytest.raises(VahanImportError, match="unmapped vehicle class"):
        load_vahan_csv(path, MAPPINGS)


def test_synthetic_fixture_is_deterministic_and_importable(tmp_path: Path) -> None:
    a, b = tmp_path / "a.csv", tmp_path / "b.csv"
    generate_synthetic_vahan_csv(a)
    generate_synthetic_vahan_csv(b)
    assert a.read_bytes() == b.read_bytes()
    rows = load_vahan_csv(a, MAPPINGS)
    assert {r.rto_code for r in rows} == set(SYNTHETIC_RTOS)


def _raster(path: Path, data: np.ndarray, nodata: float = -99999.0) -> Path:
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=data.shape[0],
        width=data.shape[1],
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=from_origin(73.80, 18.55, 0.001, 0.001),
        nodata=nodata,
    ) as dst:
        dst.write(data.astype("float32"), 1)
    return path


@settings(max_examples=25, deadline=None)
@given(
    st.lists(st.floats(0, 500, allow_nan=False), min_size=400, max_size=400),
    st.sampled_from([1, 3, 10]),
)
def test_h3_aggregation_preserves_the_raster_total(values: list[float], subsample: int) -> None:
    import tempfile

    data = np.array(values, dtype="float32").reshape(20, 20)
    data[0, 0] = -99999.0  # one nodata pixel must be excluded, not summed
    with tempfile.TemporaryDirectory() as d:
        agg = aggregate_to_h3(_raster(Path(d) / "p.tif", data), res=8, subsample=subsample)
    expected = float(data[data > 0].sum())
    assert sum(agg.cells.values()) == pytest.approx(expected, rel=1e-6, abs=1e-3)
    assert agg.pixel_total == pytest.approx(expected, rel=1e-6, abs=1e-3)

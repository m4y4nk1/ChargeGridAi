"""Grid module engines (Section 9.11) and the DISCOM file parser."""

from datetime import date

import pytest

from app.engines.grid.headroom import AssetRating, SiteLoad, UpgradeRules, assess, confidence_for
from app.engines.grid.load_profile import site_hourly_kw, split_by_share, stack, total
from app.ingestion.discom import parse_csv

RULES = UpgradeRules(
    [100, 250, 400, 630, 1000], transformer_inr_per_kva=2500, transformer_fixed_inr=300000
)
TODAY = date(2026, 9, 27)


def test_site_profile_spreads_daily_energy_by_occupancy() -> None:
    occ = [0.0] * 24
    occ[18], occ[19] = 3.0, 1.0
    hourly = site_hourly_kw(900.0, occ, 0.9)
    assert hourly[18] == pytest.approx(750.0) and hourly[19] == pytest.approx(250.0)
    assert sum(hourly) == pytest.approx(1000.0)  # energy at the meter includes losses


def test_split_and_stack() -> None:
    parts = split_by_share([10.0] * 24, {"e4w": 0.75, "ebus": 0.25, "none": 0.0})
    assert parts["e4w"][0] == 7.5 and "none" not in parts
    stacked = stack([("DC_60", [1.0] * 24), ("DC_60", [2.0] * 24), ("DC_120", [4.0] * 24)])
    assert stacked["DC_60"][5] == 3.0 and total(stacked)[5] == 7.0


def test_headroom_without_hourly_profile_adds_coincident_peak() -> None:
    asset = AssetRating("T1", "transformer", 400, 0.8, 250, date(2026, 6, 1))
    peaky = [10.0] * 24
    peaky[19] = 90.0
    result = assess(asset, [SiteLoad("s1", peaky, 120)], 0.9, 1.0, RULES, TODAY)
    assert result.capacity_kva == 320 and result.headroom_kva == 70
    assert result.added_peak_kva == pytest.approx(100.0)
    assert result.peak_with_kva == pytest.approx(350.0)
    assert result.overloaded
    # needs 350 / 0.8 = 437.5 kVA -> next standard size 630
    assert result.upgrade_to_kva == 630
    assert result.upgrade_cost_inr == 300000 + 2500 * (630 - 400)
    assert result.confidence == "MEDIUM"


def test_hourly_base_profile_uses_the_combined_curve() -> None:
    base = [300.0 if 9 <= h < 12 else 100.0 for h in range(24)]
    evening = [0.0] * 24
    evening[19] = 90.0
    asset = AssetRating("T2", "transformer", 400, 0.8, 999, date(2026, 8, 1), base)
    result = assess(asset, [SiteLoad("s1", evening, 90)], 0.9, 1.0, RULES, TODAY)
    # The site peaks at 19:00 when the base is 100 kVA, so no overload.
    assert result.base_peak_kva == 300 and result.peak_with_kva == pytest.approx(300.0)
    assert not result.overloaded and result.upgrade is None
    assert result.confidence == "HIGH"


def test_substation_overload_is_flagged_not_costed() -> None:
    asset = AssetRating("S1", "substation", 1000, 0.8, 790, None)
    result = assess(asset, [SiteLoad("s1", [20.0] * 24, 20)], 1.0, 1.0, RULES, TODAY)
    assert result.overloaded and result.upgrade_cost_inr is None
    assert "DISCOM system study" in (result.upgrade or "")
    assert result.confidence == "LOW"


def test_beyond_largest_transformer() -> None:
    asset = AssetRating("T3", "transformer", 1000, 0.8, 700, TODAY)
    result = assess(asset, [SiteLoad("s", [500.0] * 24, 500)], 1.0, 1.0, RULES, TODAY)
    assert result.upgrade_cost_inr is None and "largest standard" in (result.upgrade or "")


def test_confidence_rules() -> None:
    assert confidence_for(date(2026, 8, 1), True, TODAY, 365, 90) == "HIGH"
    assert confidence_for(date(2026, 1, 1), True, TODAY, 365, 90) == "MEDIUM"
    assert confidence_for(date(2024, 1, 1), False, TODAY, 365, 90) == "LOW"


def test_bad_inputs_rejected() -> None:
    with pytest.raises(ValueError):
        assess(AssetRating("x", "transformer", 0, 0.8, 0, None), [], 0.9, 1, RULES, TODAY)
    with pytest.raises(ValueError):
        site_hourly_kw(10, [1.0] * 23, 0.9)


def test_discom_csv_parsing_validates_rows() -> None:
    hours = ",".join(["50"] * 24)
    text = (
        "asset_code,asset_type,name,lat,lng,voltage_kv,rated_kva,base_peak_kva,measured_on,"
        "loading_limit," + ",".join(f"h{h:02d}" for h in range(24)) + "\n"
        f"DT-1,transformer,Baner DT,18.56,73.78,0.433,400,210,2026-05-01,0.8,{hours}\n"
        "SS-1,substation,Hinjewadi SS,18.59,73.74,22,20000,12500,2026-05-01,," + "," * 23 + "\n"
        "DT-1,transformer,dup,18.56,73.78,,400,200,,," + "," * 23 + "\n"
        "DT-2,feeder,x,18.56,73.78,,400,200,,," + "," * 23 + "\n"
        "DT-3,transformer,far,28.6,77.2,,400,200,,," + "," * 23 + "\n"
        "DT-4,transformer,x,18.56,73.78,,0,200,,1.5," + "," * 23 + "\n"
    )
    parsed = parse_csv(text, "MSEDCL")
    assert [r["id"] for r in parsed.rows] == ["MSEDCL:DT-1", "MSEDCL:SS-1"]
    dt = parsed.rows[0]
    assert dt["base_hourly_kva"] == [50.0] * 24 and dt["base_peak_kva"] == 50.0
    assert parsed.rows[1]["base_hourly_kva"] is None
    problems = {r["asset_code"]: r["problems"] for r in parsed.rejected}
    assert problems["DT-1"] == ["duplicate asset_code"]
    assert "asset_type" in problems["DT-2"][0]
    assert "outside every modelled region" in problems["DT-3"][0]
    assert len(problems["DT-4"]) == 2


def test_discom_csv_missing_columns() -> None:
    with pytest.raises(ValueError, match="missing columns"):
        parse_csv("asset_code,lat\nx,1\n", "MSEDCL")

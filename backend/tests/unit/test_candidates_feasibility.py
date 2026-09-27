from dataclasses import replace

import pytest

from app.engines.candidates import ROADSIDE, HostOption, RawCandidate, dedupe, pick_host
from app.engines.feasibility import Measurements, RuleConfig, evaluate

LAT, LNG = 18.52, 73.85
M_PER_DEG_LAT = 111_320


def cand(origin: str, dlat_m: float = 0.0, host_type: str = "FUEL_STATION") -> RawCandidate:
    return RawCandidate(
        origin=origin, lat=LAT + dlat_m / M_PER_DEG_LAT, lng=LNG, host_type=host_type
    )


# --- dedupe ----------------------------------------------------------------------

PRIORITY = ["user", "poi", "gap_cell", "corridor"]


def test_dedupe_keeps_the_highest_priority_origin_and_records_the_others() -> None:
    kept = dedupe([cand("corridor"), cand("poi", 100), cand("gap_cell", 50)], 150, PRIORITY)
    assert len(kept) == 1
    assert kept[0].candidate.origin == "poi"
    assert sorted(kept[0].merged_origins) == ["corridor", "gap_cell"]
    assert kept[0].merged_count == 2


def test_dedupe_keeps_points_further_apart_than_the_radius() -> None:
    assert len(dedupe([cand("poi"), cand("poi", 200)], 150, PRIORITY)) == 2


def test_dedupe_prefers_a_hosted_site_over_roadside_at_equal_priority() -> None:
    kept = dedupe(
        [cand("corridor", 0, ROADSIDE), cand("corridor", 20, "FUEL_STATION")], 150, PRIORITY
    )
    assert [k.candidate.host_type for k in kept] == ["FUEL_STATION"]


def test_dedupe_finds_neighbours_across_grid_cell_edges() -> None:
    # Many points on a 140 m chain: each is within 150 m of the next.
    chain = [cand("poi", i * 140) for i in range(10)]
    kept = dedupe(chain, 150, PRIORITY)
    positions = [round((k.candidate.lat - LAT) * M_PER_DEG_LAT) for k in kept]
    assert all(b - a > 150 for a, b in zip(positions, positions[1:], strict=False))


def test_pick_host_prefers_host_type_then_distance() -> None:
    options = [
        HostOption("n1", "PARKING", None, LAT, LNG, 100),
        HostOption("n2", "FUEL_STATION", None, LAT, LNG, 800),
        HostOption("n3", "FUEL_STATION", None, LAT, LNG, 1200),
    ]
    chosen = pick_host(options, ["FUEL_STATION", "PARKING"], within_m=1000)
    assert chosen is not None and chosen.poi_id == "n2"
    assert pick_host(options[2:], ["FUEL_STATION"], within_m=1000) is None


# --- feasibility --------------------------------------------------------------------

CFG = RuleConfig(
    enabled={f"F0{i}": True for i in range(1, 8)} | {"F06": False},
    max_road_distance_m=50,
    min_dc_spacing_m=1000,
    served_access_ratio=1.0,
    min_grid_confidence="MEDIUM",
    max_substation_distance_m=5000,
    max_arterial_distance_m=300,
)

GOOD = Measurements(
    restricted_hits=[],
    landuse=["commercial"],
    nearest_road_m=12.0,
    nearest_road_class="primary",
    nearest_arterial_m=12.0,
    nearest_dc_station_m=4000.0,
    cell_access_ratio=0.2,
    nearest_substation_m=900.0,
    grid_confidence="LOW",
)


def test_a_well_placed_site_passes_and_every_rule_is_accounted_for() -> None:
    v = evaluate(GOOD, CFG, dc=True)
    assert v.passed and v.reason_codes == []
    assert set(v.details) == {f"F0{i}" for i in range(1, 8)}
    assert v.details["F06"]["outcome"] == "disabled"
    assert v.details["F07"]["outcome"] == "deferred"


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"restricted_hits": [{"class": "water", "tag": "water", "name": "Pashan Lake"}]}, "F01"),
        ({"nearest_road_m": 80.0}, "F02"),
        ({"nearest_road_m": None}, "F02"),
        ({"nearest_dc_station_m": 400.0, "cell_access_ratio": 1.4}, "F03"),
        (
            {
                "landuse": ["residential"],
                "nearest_road_class": "residential",
                "nearest_arterial_m": 700.0,
            },
            "F05",
        ),
    ],
)
def test_each_rule_rejects_with_its_code_and_measurement(
    change: dict[str, object], code: str
) -> None:
    v = evaluate(replace(GOOD, **change), CFG, dc=True)  # type: ignore[arg-type]
    assert not v.passed and v.reason_codes == [code]
    assert v.details[code]["outcome"] == "fail"
    assert "measured" in v.details[code]


def test_nearby_dc_station_is_fine_where_demand_is_not_yet_met() -> None:
    v = evaluate(replace(GOOD, nearest_dc_station_m=400.0, cell_access_ratio=0.3), CFG, dc=True)
    assert v.passed


def test_low_confidence_grid_data_never_rejects() -> None:
    v = evaluate(replace(GOOD, nearest_substation_m=50_000.0), CFG, dc=True)
    assert v.passed
    assert v.details["F04"]["outcome"] == "not_applied"
    assert v.details["F04"]["measured"] == 50_000.0


def test_grid_rule_applies_once_grid_data_is_trustworthy() -> None:
    v = evaluate(replace(GOOD, nearest_substation_m=9000.0, grid_confidence="MEDIUM"), CFG, dc=True)
    assert v.reason_codes == ["F04"]


def test_dc_only_rules_are_not_applied_to_ac_scenarios() -> None:
    residential = replace(
        GOOD,
        landuse=["residential"],
        nearest_road_class="residential",
        nearest_arterial_m=900.0,
        nearest_dc_station_m=100.0,
        cell_access_ratio=2.0,
    )
    v = evaluate(residential, CFG, dc=False)
    assert v.passed
    assert v.details["F03"]["outcome"] == v.details["F05"]["outcome"] == "not_applied"


def test_multiple_failures_are_all_reported() -> None:
    v = evaluate(
        replace(GOOD, nearest_road_m=500.0, restricted_hits=[{"class": "forest"}]), CFG, dc=True
    )
    assert v.reason_codes == ["F01", "F02"]

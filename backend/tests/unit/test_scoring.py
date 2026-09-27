import numpy as np
import pytest

from app.engines.scoring import (
    SUB_SCORES,
    cagr,
    index_vs_benchmark,
    percentile_rank,
    rank_order,
    rank_robustness,
    score_matrix,
    totals,
    validate_weights,
)

CPO = {
    "demand": 0.25,
    "accessibility": 0.15,
    "traffic": 0.15,
    "grid": 0.15,
    "land": 0.10,
    "future_growth": 0.10,
    "competition_gap": 0.10,
    "equity": 0.0,
}


def test_percentile_rank_spans_0_to_100_and_shares_ties() -> None:
    assert percentile_rank([10, 30, 20]).tolist() == [0.0, 100.0, 50.0]
    assert percentile_rank([5, 5, 9]).tolist() == [25.0, 25.0, 100.0]
    assert percentile_rank([1.0]).tolist() == [100.0]
    assert percentile_rank([]).tolist() == []


def test_percentile_rank_lower_is_better_for_distances() -> None:
    assert percentile_rank([100.0, 5000.0, 900.0], higher_is_better=False).tolist() == [
        100.0,
        0.0,
        50.0,
    ]


def test_weights_validated_and_normalised() -> None:
    w = validate_weights(CPO)
    assert sum(w.values()) == pytest.approx(1.0)
    assert set(w) == set(SUB_SCORES)
    assert validate_weights({"demand": 2, "grid": 2})["demand"] == 0.5
    with pytest.raises(ValueError, match="unknown"):
        validate_weights({"vibes": 1})
    with pytest.raises(ValueError):
        validate_weights({"demand": 0})
    with pytest.raises(ValueError):
        validate_weights({"demand": -1, "grid": 2})


def test_total_is_weighted_sum_and_ranks_best_first() -> None:
    subs = [
        dict.fromkeys(SUB_SCORES, 50.0),
        dict.fromkeys(SUB_SCORES, 100.0),
        {**dict.fromkeys(SUB_SCORES, 0.0), "demand": 100.0},
    ]
    m = score_matrix(subs)
    t = totals(m, validate_weights(CPO))
    assert t.tolist() == pytest.approx([50.0, 100.0, 25.0])
    assert rank_order(t).tolist() == [1, 0, 2]


def test_ranks_are_stable_for_ties() -> None:
    assert rank_order(np.array([5.0, 7.0, 5.0, 7.0])).tolist() == [1, 3, 0, 2]


def test_robustness_dominant_site_always_top_and_dominated_never() -> None:
    rng = np.random.default_rng(0)
    m = rng.uniform(20, 80, size=(50, len(SUB_SCORES)))
    m[0] = 100.0  # best on every factor
    m[1] = 0.0  # worst on every factor
    r = rank_robustness(m, validate_weights(CPO), top_n=10, draws=500, concentration=100, seed=1)
    assert r[0] == 1.0
    assert r[1] == 0.0
    # Each draw puts exactly N sites in the top N.
    assert r.sum() == pytest.approx(10.0)


def test_robustness_is_seeded_and_ignores_zero_weights() -> None:
    rng = np.random.default_rng(3)
    m = rng.uniform(0, 100, size=(30, len(SUB_SCORES)))
    w = validate_weights(CPO)
    a = rank_robustness(m, w, 5, 200, 100, seed=7)
    b = rank_robustness(m, w, 5, 200, 100, seed=7)
    assert a.tolist() == b.tolist()
    # Changing only the equity column (weight 0 in this profile) changes nothing.
    m2 = m.copy()
    m2[:, SUB_SCORES.index("equity")] = 0.0
    assert rank_robustness(m2, w, 5, 200, 100, seed=7).tolist() == a.tolist()


def test_robustness_when_everyone_fits() -> None:
    m = np.ones((3, len(SUB_SCORES)))
    assert rank_robustness(m, validate_weights(CPO), 5, 10, 100, 0).tolist() == [1.0, 1.0, 1.0]


def test_cagr_and_index() -> None:
    assert cagr(100, 200, 4) == pytest.approx(2**0.25 - 1)
    assert cagr(0, 10, 4) is None
    assert cagr(10, 20, 0) is None
    assert index_vs_benchmark(150, 100) == 150.0
    assert index_vs_benchmark(5, 0) is None
    assert index_vs_benchmark(None, 10) is None


def test_growth_window_caps_at_forecast_end() -> None:
    from app.services.scoring_run import _growth_window

    assert _growth_window(2028, 4) == (2028, 2032, None)
    start, end, note = _growth_window(2033, 4)
    assert (start, end) == (2033, 2035)
    assert note is not None and "2035" in note
    # Target at the forecast end: look back instead of forward.
    start, end, note = _growth_window(2035, 4)
    assert (start, end) == (2031, 2035)
    assert note is not None


def test_catchment_cells_include_site_cell_and_respect_holes() -> None:
    import h3
    from shapely.geometry import MultiPolygon, Polygon

    from app.services.scoring_run import H3_RES, _cells_in

    lat, lng = 18.5204, 73.8567
    outer = [
        (lng - 0.03, lat - 0.03),
        (lng + 0.03, lat - 0.03),
        (lng + 0.03, lat + 0.03),
        (lng - 0.03, lat + 0.03),
    ]
    hole = [
        (lng - 0.01, lat - 0.01),
        (lng + 0.01, lat - 0.01),
        (lng + 0.01, lat + 0.01),
        (lng - 0.01, lat + 0.01),
    ]
    solid = _cells_in(MultiPolygon([Polygon(outer)]), lat, lng)
    holed = _cells_in(MultiPolygon([Polygon(outer, [hole])]), lat, lng)
    site_cell = h3.latlng_to_cell(lat, lng, H3_RES)
    assert site_cell in solid and site_cell in holed  # the site's own cell is always served
    assert len(holed) < len(solid)
    # A sliver too thin to contain any cell centre still yields the site's cell.
    sliver = MultiPolygon([Polygon([(lng, lat), (lng + 1e-5, lat), (lng + 1e-5, lat + 1e-5)])])
    assert _cells_in(sliver, lat, lng) == [site_cell]

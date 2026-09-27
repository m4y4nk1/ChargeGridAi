import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.core.assumptions import Assumptions, MissingAssumption
from app.engines.demand.adoption_bass import (
    BassParams,
    bass_monthly,
    cagr_baseline,
    fit_bass,
    forecast_stock,
)
from app.engines.demand.allocation import allocation_shares
from app.engines.demand.charging_demand import SegmentUsage, public_kwh_per_day
from app.engines.demand.stock import stock_from_registrations
from app.engines.supply_gap import (
    SupplyAssumptions,
    additional_charge_points,
    station_supply,
    two_step_fca,
)

TRUE = BassParams(m=60_000, p=0.004, q=0.9)


# --- assumptions -----------------------------------------------------------


def test_assumptions_prefer_sourced_values_and_track_placeholders() -> None:
    a = Assumptions()
    assert a.get({"value": 0.5, "source": "MoRTH"}, "x") == 0.5
    assert a.get({"value": "REPLACE_ME", "illustrative": 0.2}, "y") == 0.2
    assert a.placeholders_in_use == ["y"]
    with pytest.raises(MissingAssumption):
        a.get({"value": "REPLACE_ME"}, "z")


# --- stock -------------------------------------------------------------------


def test_stock_without_scrappage_is_cumulative_registrations() -> None:
    regs = np.array([10.0, 20.0, 30.0])
    assert stock_from_registrations(regs, 0.0).tolist() == [10.0, 30.0, 60.0]


def test_scrappage_removes_the_expected_share_after_a_year() -> None:
    regs = np.zeros(13)
    regs[0] = 1000.0
    assert stock_from_registrations(regs, 0.10)[12] == pytest.approx(900.0)


# --- Bass --------------------------------------------------------------------


def test_bass_fit_recovers_a_known_curve() -> None:
    fit = fit_bass(bass_monthly(72, TRUE))
    assert fit.params is not None
    assert fit.params.m == pytest.approx(TRUE.m, rel=0.05)
    assert fit.params.q == pytest.approx(TRUE.q, rel=0.1)


def test_bass_beats_the_naive_cagr_baseline_on_a_holdout() -> None:
    rng = np.random.default_rng(7)
    full = bass_monthly(84, TRUE) * rng.uniform(0.9, 1.1, 84)
    train, holdout = full[:60], full[60:]

    fit = fit_bass(train)
    assert fit.params is not None
    bass_error = np.abs(bass_monthly(84, fit.params)[60:] - holdout).mean()
    cagr_error = np.abs(cagr_baseline(train, 84)[60:] - holdout).mean()
    assert bass_error < cagr_error


def test_forecast_quantiles_are_ordered_and_history_is_untouched() -> None:
    obs = bass_monthly(60, TRUE)
    f = forecast_stock(obs, 120, annual_scrappage=0.05, n_bootstrap=40, seed=1)
    assert np.all(f.p10 <= f.p50 + 1e-9) and np.all(f.p50 <= f.p90 + 1e-9)
    observed_stock = stock_from_registrations(obs, 0.05)
    assert f.p50[:60] == pytest.approx(observed_stock)


def test_forecast_is_reproducible_for_a_seed() -> None:
    obs = bass_monthly(60, TRUE)
    a = forecast_stock(obs, 96, 0.05, n_bootstrap=20, seed=3)
    b = forecast_stock(obs, 96, 0.05, n_bootstrap=20, seed=3)
    assert np.array_equal(a.p90, b.p90)


def test_multiplier_scales_only_future_registrations() -> None:
    obs = bass_monthly(60, TRUE)
    base = forecast_stock(obs, 72, 0.0, n_bootstrap=0)
    fast = forecast_stock(obs, 72, 0.0, multiplier=1.3, n_bootstrap=0)
    assert fast.p50[59] == pytest.approx(base.p50[59])
    added_base = base.p50[-1] - base.p50[59]
    added_fast = fast.p50[-1] - fast.p50[59]
    assert added_fast == pytest.approx(1.3 * added_base)


def test_forecast_stock_never_falls_even_after_adoption_saturates() -> None:
    # A curve that is already past its adoption peak: Bass adoptions shrink to ~0,
    # but scrapped EVs are replaced, so the fleet must not decline.
    obs = bass_monthly(96, BassParams(m=20_000, p=0.02, q=1.2))
    f = forecast_stock(obs, 240, annual_scrappage=0.10, n_bootstrap=20, seed=2)
    assert np.all(np.diff(f.p50[95:]) >= -1e-6)
    assert np.all(np.diff(f.p10[95:]) >= -1e-6)


def test_thin_series_fall_back_to_a_flagged_flat_projection() -> None:
    f = forecast_stock(np.array([0.0] * 20 + [1.0, 2.0]), 40, 0.0, n_bootstrap=10)
    assert f.method == "flat_trailing_mean"
    assert f.params is None


# --- allocation ---------------------------------------------------------------


@settings(max_examples=60, deadline=None)
@given(
    n=st.integers(1, 60),
    total=st.floats(0, 1e6, allow_nan=False),
    data=st.data(),
)
def test_allocation_preserves_rto_totals_within_half_a_percent(
    n: int, total: float, data: st.DataObject
) -> None:
    features = {
        name: np.array(
            data.draw(st.lists(st.floats(0, 1e5, allow_nan=False), min_size=n, max_size=n))
        )
        for name in ("population", "commerce", "transit")
    }
    weights = {"population": 0.5, "commerce": 0.3, "transit": 0.2}
    allocated = total * allocation_shares(features, weights, n)
    assert allocated.sum() == pytest.approx(total, rel=0.005, abs=1e-6)
    assert (allocated >= 0).all()


def test_allocation_follows_features_and_redistributes_empty_ones() -> None:
    shares = allocation_shares(
        {"population": np.array([3.0, 1.0]), "transit": np.array([0.0, 0.0])},
        {"population": 0.5, "transit": 0.5},
        2,
    )
    assert shares.tolist() == pytest.approx([0.75, 0.25])


def test_allocation_is_uniform_when_every_feature_is_empty() -> None:
    shares = allocation_shares({"population": np.zeros(4)}, {"population": 1.0}, 4)
    assert shares.tolist() == [0.25] * 4


# --- demand ------------------------------------------------------------------


def test_public_kwh_formula() -> None:
    usage = SegmentUsage(
        annual_km=36_500, kwh_per_km=0.1, public_share=0.5, kwh_per_public_session=5
    )
    # 100 vehicles x 100 km/day x 0.1 kWh/km x 0.5 public = 500 kWh/day
    assert public_kwh_per_day(100.0, usage) == pytest.approx(500.0)


# --- supply & gap ---------------------------------------------------------------

ASSUME = SupplyAssumptions(
    availability=1.0,
    target_utilisation=0.25,
    assumed_kw_when_unknown=10.0,
    assumed_charge_points_when_unknown=1,
    new_charge_point_kw=10.0,
)


def test_station_supply_uses_known_kw_and_flags_assumptions() -> None:
    s = station_supply("a", [(20.0, 2), (None, 1)], ASSUME)
    assert s.kwh_per_day == pytest.approx(2 * 20 * 6 + 10 * 6)  # 0.25 x 24 h = 6 h/day
    assert (s.charge_points, s.assumed_kw_charge_points, s.assumed_count) == (3, 1, False)
    empty = station_supply("b", [], ASSUME)
    assert empty.assumed_count and empty.charge_points == 1


def test_no_stations_means_the_whole_demand_is_gap() -> None:
    demand = np.array([10.0, 20.0])
    result = two_step_fca(demand, [], [])
    assert result.gap_kwh.tolist() == [10.0, 20.0]


def test_two_step_fca_shares_one_station_across_its_catchment() -> None:
    demand = np.array([30.0, 10.0, 50.0])
    result = two_step_fca(demand, [20.0], [[0, 1]])
    # 20 kWh of supply over 40 kWh of catchment demand -> both cells half-served
    assert result.access_ratio.tolist() == pytest.approx([0.5, 0.5, 0.0])
    assert result.served_kwh.sum() == pytest.approx(20.0)
    assert result.gap_kwh.tolist() == pytest.approx([15.0, 5.0, 50.0])


def test_ample_supply_closes_the_gap_without_exceeding_demand() -> None:
    result = two_step_fca(np.array([5.0]), [100.0], [[0]])
    assert result.gap_kwh.tolist() == [0.0]
    assert result.served_kwh.tolist() == [5.0]


def test_supply_with_no_demand_in_catchment_is_reported_unused() -> None:
    assert two_step_fca(np.array([0.0, 5.0]), [12.0], [[0]]).unused_supply_kwh == 12.0


def test_additional_charge_points_rounds_up() -> None:
    assert additional_charge_points(61.0, ASSUME) == 2  # 60 kWh/day per new point
    assert additional_charge_points(0.0, ASSUME) == 0

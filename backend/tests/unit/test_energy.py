import pytest

from app.engines.energy.dispatch import (
    EnergyProblem,
    annuity_factor,
    pv_capacity_factor,
    solve_dispatch,
)

DAYS = [31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
# A crude solar day: 6 h of full sun around noon.
SUN = [0.0] * 9 + [0.8] * 6 + [0.0] * 9


def _problem(**kw: object) -> EnergyProblem:
    base = dict(
        load_kw=[100.0] * 24,
        cf=[SUN] * 12,
        days=DAYS,
        price=[8.0] * 24,
        demand_charge_kva_month=100.0,
        power_factor=1.0,
        design_peak_kw=150.0,
        pv_max_kwp=0.0,
        allow_battery=False,
        allow_pv=False,
        contract_cap_kw=None,
        annual_cost_per_kwp=5000.0,
        annual_cost_per_kwh=3000.0,
        annual_cost_per_kw=1000.0,
        round_trip_efficiency=0.9,
        min_soc=0.1,
        max_soc=0.95,
        peak_duration_h=0.5,
    )
    base.update(kw)
    return EnergyProblem(**base)  # type: ignore[arg-type]


def test_annuity_and_capacity_factor() -> None:
    assert annuity_factor(0.0, 10) == pytest.approx(0.1)
    # 12% over 10 years: 0.1770 (standard table value).
    assert annuity_factor(0.12, 10) == pytest.approx(0.17698, abs=1e-5)
    assert pv_capacity_factor(0, 30, 0.8, -0.004, 25) == 0.0
    # 1000 W/m2 at 25 degC air: cell 56.25 degC -> 12.5% hotter-cell loss on PR 0.8.
    assert pv_capacity_factor(1000, 25, 0.8, -0.004, 25) == pytest.approx(0.8 * (1 - 0.004 * 31.25))


def test_grid_only_baseline_costs() -> None:
    r = solve_dispatch(_problem())
    assert r.status == "optimal"
    assert r.contract_kw == pytest.approx(150.0)  # the design peak sets the contract
    assert r.grid_kwh == pytest.approx(100 * 24 * 366)
    assert r.annual_energy_cost == pytest.approx(8 * 100 * 24 * 366)
    assert r.annual_demand_cost == pytest.approx(100 * 12 * 150)
    assert r.pv_kwp == 0 and r.battery_kwh == 0


def test_cheap_pv_is_built_up_to_its_limit_and_used() -> None:
    # 1 kWp makes 0.8 x 6 h x 366 d = 1,756.8 kWh/yr, worth ~14,054 INR at 8 INR/kWh.
    r = solve_dispatch(_problem(allow_pv=True, pv_max_kwp=100.0))
    assert r.pv_kwp == pytest.approx(100.0)
    assert r.pv_used_kwh == pytest.approx(r.pv_generated_kwh)  # 80 kW < 100 kW load: no curtailment
    assert r.grid_kwh == pytest.approx(100 * 24 * 366 - 80 * 6 * 366)


def test_expensive_pv_is_not_built() -> None:
    r = solve_dispatch(_problem(allow_pv=True, pv_max_kwp=100.0, annual_cost_per_kwp=20_000.0))
    assert r.pv_kwp == pytest.approx(0.0, abs=1e-6)


def test_battery_shaves_the_contract_when_demand_charges_are_high() -> None:
    # Demand charge 2,000/kVA-month: each kW of contract costs 24,000/yr, battery kW 1,000.
    r = solve_dispatch(_problem(allow_battery=True, demand_charge_kva_month=2000.0))
    assert r.contract_kw == pytest.approx(100.0)  # hourly load must still come from the grid
    assert r.battery_kw == pytest.approx(50.0)  # covers the sub-hourly peak above it
    # ...and holds enough energy to deliver it for the peak's half hour.
    assert r.battery_kwh * (0.95 - 0.1) >= 50.0 * 0.5 - 1e-6


def test_contract_cap_forces_storage_or_is_infeasible() -> None:
    capped = solve_dispatch(_problem(allow_battery=True, contract_cap_kw=120.0))
    assert capped.contract_kw <= 120.0 + 1e-6
    assert capped.contract_kw + capped.battery_kw >= 150.0 - 1e-6
    # Without a battery, a cap below the average load can't be met.
    assert solve_dispatch(_problem(contract_cap_kw=90.0)).status == "infeasible"


def test_time_of_day_arbitrage_charges_at_night() -> None:
    night_cheap = [4.0] * 6 + [8.0] * 12 + [12.0] * 6
    r = solve_dispatch(
        _problem(
            allow_battery=True,
            price=night_cheap,
            annual_cost_per_kwh=500.0,
            annual_cost_per_kw=100.0,
            demand_charge_kva_month=0.0,
        )
    )
    assert r.battery_kwh > 0
    ch = r.dispatch["ch"][0]
    dis = r.dispatch["dis"][0]
    assert sum(ch[:6]) > 0 and sum(dis[18:]) > 0
    assert sum(ch[18:]) == pytest.approx(0.0, abs=1e-6)


def test_hourly_prices_and_load_weighting() -> None:
    from app.core.assumptions import Assumptions
    from app.services.tariffs import hourly_prices, load_weighted_price

    tariff = {
        "connection_type": "LT",
        "energy_charge_inr_per_kwh": {"value": 8.0},
        "tod_slabs": [
            {"start": 0, "end": 6, "adder_inr_per_kwh": {"illustrative": -1.5}},
            {"start": 18, "end": 22, "adder_inr_per_kwh": {"illustrative": 1.0}},
        ],
    }
    a = Assumptions()
    prices = hourly_prices(tariff, a)
    assert prices[3] == 6.5 and prices[12] == 8.0 and prices[20] == 9.0
    assert a.placeholders_in_use == ["tariffs.LT.tod.0", "tariffs.LT.tod.1"]
    evening = [0.0] * 18 + [1.0] * 4 + [0.0] * 2
    assert load_weighted_price(prices, evening) == 9.0
    assert load_weighted_price(prices, [0.0] * 24) == sum(prices) / 24

import pytest

from app.engines.finance.project import (
    FinanceInputs,
    Triangular,
    cases,
    cash_flows,
    irr,
    monte_carlo,
    npv,
    payback,
    tornado,
)


def _inputs(**kw: object) -> FinanceInputs:
    base = dict(
        served_kwh_per_day=1000.0,
        capacity_kwh_per_day=2000.0,
        capex_lines={"chargers": 6_000_000.0, "civil": 1_000_000.0},
        selling_price=20.0,
        energy_price=8.0,
        charger_efficiency=1.0,
        demand_charge_per_kva_month=0.0,
        sanctioned_kva=0.0,
        maintenance_pct_capex=0.0,
        insurance_pct_capex=0.0,
        rent_share=0.0,
        roaming_share=0.0,
        payment_share=0.0,
        staff_per_year=0.0,
        ramp=[0.5, 1.0],
        growth=0.0,
        discount_rate=0.1,
        horizon=5,
    )
    base.update(kw)
    return FinanceInputs(**base)  # type: ignore[arg-type]


def test_npv_irr_payback_on_a_known_stream() -> None:
    flows = [-100.0, 60.0, 60.0]
    assert npv(flows, 0.0) == pytest.approx(20.0)
    rate = irr(flows)
    assert rate is not None and npv(flows, rate) == pytest.approx(0.0, abs=1e-6)
    assert rate == pytest.approx(0.1307, abs=1e-4)
    assert payback(flows) == pytest.approx(1 + 40 / 60)
    assert irr([-100.0, -1.0]) is None
    assert payback([-100.0, 10.0, 10.0]) is None


def test_cash_flows_ramp_margin_and_capacity_cap() -> None:
    cf = cash_flows(_inputs())
    # Year 1 at 50% ramp: 500 kWh/day x 365 x (20 - 8) margin.
    assert cf.ebitda[1] == pytest.approx(500 * 365 * 12)
    assert cf.ebitda[2] == pytest.approx(1000 * 365 * 12)
    assert cf.net[0] == -7_000_000
    capped = cash_flows(_inputs(served_kwh_per_day=5000.0))
    assert capped.energy_kwh[2] == pytest.approx(2000 * 365)  # capacity binds


def test_losses_fixed_costs_and_revenue_shares_reduce_ebitda() -> None:
    lean = cash_flows(_inputs()).ebitda[2]
    lossy = cash_flows(_inputs(charger_efficiency=0.9)).ebitda[2]
    assert lossy == pytest.approx(lean - 1000 * 365 * 8 * (1 / 0.9 - 1))
    shared = cash_flows(_inputs(rent_share=0.1, staff_per_year=100_000.0)).ebitda[2]
    assert shared == pytest.approx(lean - 0.1 * 1000 * 365 * 20 - 100_000)
    demand = cash_flows(_inputs(demand_charge_per_kva_month=100.0, sanctioned_kva=200.0))
    assert demand.ebitda[2] == pytest.approx(lean - 100 * 200 * 12)


def test_growth_applies_after_the_ramp() -> None:
    cf = cash_flows(_inputs(growth=0.1, capacity_kwh_per_day=1e9))
    assert cf.energy_kwh[3] == pytest.approx(1000 * 365 * 1.1)
    assert cf.energy_kwh[4] == pytest.approx(1000 * 365 * 1.21)


DISTS = {
    "demand": Triangular(0.6, 1.0, 1.3),
    "selling_price": Triangular(0.85, 1.0, 1.1),
    "capex": Triangular(0.9, 1.0, 1.3),
}


def test_monte_carlo_quantiles_ordered_and_seeded() -> None:
    f = _inputs()
    a = monte_carlo(f, DISTS, draws=2000, seed=3)
    b = monte_carlo(f, DISTS, draws=2000, seed=3)
    assert a == b
    q = a["npv"]
    assert q["p10"] is not None and q["p50"] is not None and q["p90"] is not None
    assert q["p10"] < q["p50"] < q["p90"]
    assert 0.0 <= a["prob_npv_positive"]["p"] <= 1.0  # type: ignore[operator]


def test_payback_never_is_the_worst_outcome() -> None:
    # A site that never recovers its CAPEX in most draws: P90 payback is "never" (None).
    poor = _inputs(served_kwh_per_day=20.0)
    out = monte_carlo(poor, DISTS, draws=500, seed=1)
    assert out["payback_years"]["p90"] is None


def test_tornado_and_cases() -> None:
    f = _inputs()
    rows = tornado(f, DISTS)
    swings = [float(r["swing"]) for r in rows]
    assert swings == sorted(swings, reverse=True)
    assert rows[0]["input"] == "demand"
    c = cases(
        f,
        DISTS,
        {
            "pessimistic": {"demand": "low", "selling_price": "low", "capex": "high"},
            "base": {"demand": "mode", "selling_price": "mode", "capex": "mode"},
            "optimistic": {"demand": "high", "selling_price": "high", "capex": "low"},
        },
    )
    assert c["pessimistic"].npv < c["base"].npv < c["optimistic"].npv
    assert c["base"].npv == pytest.approx(cash_flows(f).npv)

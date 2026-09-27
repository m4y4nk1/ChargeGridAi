import pytest

from app.engines.optimisation.mclp import (
    Bundle,
    CostAssumptions,
    Problem,
    bundle,
    charger_kw,
    deciding_factor,
    solve,
    why_not,
)

COSTS = CostAssumptions(
    charger_inr={"DC_60": 1_200_000, "DC_120": 2_200_000},
    installation_pct=0.10,
    civil_inr=500_000,
    canopy_inr=300_000,
    software_inr=100_000,
    lt_connection_inr=200_000,
    ht_connection_inr=1_000_000,
    transformer_inr=1_500_000,
    lt_max_sanctioned_kw=150,
    diversity_factor=0.8,
    contingency_pct=0.10,
)


def _b(key: str, cost: float, cap: float) -> Bundle:
    return Bundle(key, "DC_60", 1, 60, 48, "LT", cost, {}, cap)


def test_bundle_costs_switch_to_ht_above_the_lt_limit() -> None:
    assert charger_kw("DC_120") == 120.0
    small = bundle("DC_60", 2, COSTS, kwh_per_kw_day=5.7)
    assert small.connection == "LT" and small.sanctioned_kw == 96.0
    # 2.4M chargers + 0.24M install + 0.5 + 0.3 + 0.1 + 0.2 LT = 3.74M, +10% = 4.114M
    assert small.cost_inr == 4_114_000
    assert small.capacity_kwh == pytest.approx(120 * 5.7)
    big = bundle("DC_60", 4, COSTS, kwh_per_kw_day=5.7)
    assert big.connection == "HT"
    assert big.cost_breakdown["transformer"] == 1_500_000
    assert big.cost_inr > 2 * small.cost_inr - COSTS.civil_inr * 2  # HT premium shows


def _problem(**kw: object) -> Problem:
    # Three sites; site 0 and 1 overlap on cell 0; site 2 alone covers cell 2.
    base = dict(
        site_ids=["a", "b", "c"],
        site_value=[1.0, 1.0, 1.0],
        site_cells=[[0, 1], [0], [2]],
        bundles=[_b("small", 10, 100.0), _b("big", 25, 300.0)],
        demand=[250.0, 50.0, 80.0],
        coverage_weight=[0.0, 0.0, 0.0],
        budget_inr=100.0,
        max_sites=3,
    )
    base.update(kw)
    return Problem(**base)  # type: ignore[arg-type]


def test_mclp_respects_budget_capacity_and_demand() -> None:
    s = solve(_problem(budget_inr=35.0))
    assert s.status == "optimal"
    assert s.cost_inr <= 35.0
    # Best: big at a (serves 250 + 50 = 300) + small at c (80) = 35 -> 380 kWh.
    assert s.total_served_kwh == pytest.approx(380.0)
    assert s.open_bundle == {0: 1, 2: 0}
    for (i, _), kwh in s.served_by_cell.items():
        assert kwh <= [250.0, 50.0, 80.0][i] + 1e-6


def test_site_limit_and_spacing() -> None:
    s = solve(_problem(max_sites=1))
    assert len(s.open_bundle) == 1 and s.total_served_kwh == pytest.approx(300.0)
    # a and b may not both open; with a excluded, b takes cell 0.
    s2 = solve(_problem(spacing_pairs=[(0, 1)], excluded=frozenset({0})))
    assert 0 not in s2.open_bundle and 1 in s2.open_bundle


def test_lambda_stops_buying_capacity_that_cannot_be_used() -> None:
    # c only has 80 kWh of demand: with a cost term, the small bundle wins there.
    s = solve(_problem(lambda_per_inr=0.5))
    assert s.open_bundle.get(2) == 0


def test_population_coverage_term() -> None:
    # No kWh value, only coverage of cell 2's population: the model opens c.
    s = solve(_problem(alpha=0.0, beta=1.0, coverage_weight=[0.0, 0.0, 10.0], max_sites=1))
    assert list(s.open_bundle) == [2]
    assert s.covered[2] == pytest.approx(1.0)


def test_infeasible_when_forced_site_cannot_be_afforded() -> None:
    s = solve(_problem(budget_inr=5.0, forced_open=frozenset({0})))
    assert s.status == "infeasible"
    assert s.objective is None


def test_why_not_reports_the_trade() -> None:
    base = solve(_problem(budget_inr=35.0))
    forced = solve(_problem(budget_inr=35.0, forced_open=frozenset({1})))
    w = why_not(base, forced, site=1)
    assert w.objective_delta <= 1e-6
    assert w.served_delta_kwh <= 1e-6
    assert w.displaced  # something had to close to afford b
    name, pts = deciding_factor(
        {"demand": 40, "grid": 90}, {"demand": 95, "grid": 60}, {"demand": 0.5, "grid": 0.5}
    )
    assert name == "demand" and pts == pytest.approx(27.5)


def test_zones_merge_identically_covered_cells_losslessly() -> None:
    # Cells 0 and 1 are both covered by sites {0, 1}: one zone, split back pro rata.
    p = _problem(site_cells=[[0, 1], [0, 1], [2]], demand=[60.0, 40.0, 80.0], budget_inr=10.0)
    s = solve(p)
    served = {i: sum(v for (c, _), v in s.served_by_cell.items() if c == i) for i in range(3)}
    assert served[0] == pytest.approx(60.0) and served[1] == pytest.approx(40.0)


def test_greedy_plan_is_feasible_and_evaluated_exactly() -> None:
    from app.engines.optimisation.mclp import evaluate, greedy, solve_best

    p = _problem(budget_inr=35.0, spacing_pairs=[(0, 1)])
    plan = greedy(p)
    assert sum(p.bundles[k].cost_inr for k in plan.values()) <= 35.0
    assert not {0, 1} <= plan.keys()
    ev = evaluate(p, plan)
    full = solve(p, fixed=plan)
    assert ev.objective == pytest.approx(full.objective)
    assert ev.open_bundle == full.open_bundle
    best = solve_best(p)
    assert best.status == "optimal"
    assert best.objective == pytest.approx(solve(p).objective)


def test_solve_best_drops_bounds_below_the_incumbent() -> None:
    from app.engines.optimisation import mclp

    p = _problem(budget_inr=35.0)
    real_solve = mclp.solve

    def fake(p: Problem, *a: object, **kw: object) -> mclp.Solution:
        if "fixed" not in kw:
            # The MIP call: timed out with no incumbent and a nonsense bound of 0.
            return mclp.Solution("not_solved", None, 0.0, {}, {}, {}, {}, 0.0, 1.0, "HIGHS")
        return real_solve(p, *a, **kw)  # type: ignore[arg-type]  # plan evaluation

    mclp.solve = fake  # type: ignore[assignment]
    try:
        best = mclp.solve_best(p)
    finally:
        mclp.solve = real_solve  # type: ignore[assignment]
    assert best.status == "heuristic"
    assert best.bound is None and best.gap is None
    assert best.objective == pytest.approx(380.0)


def test_service_floor_minimises_cost_for_a_coverage_target() -> None:
    from app.engines.optimisation.mclp import greedy

    # Cost matters most (tiny kWh weight), but at least 150 kWh must be served.
    p = _problem(alpha=0.001, lambda_per_inr=1.0, min_served_kwh=150.0)
    s = solve(p)
    assert s.total_served_kwh >= 150.0 - 1e-6
    # Cheapest way to 150: one small bundle (100) is not enough; big at a (300) costs 25,
    # two smalls (a: 100, c: 80) cost 20.
    assert s.cost_inr == pytest.approx(20.0)
    plan = greedy(p)
    assert sum(p.bundles[k].cost_inr for k in plan.values()) <= 25.0


def test_gap_is_only_reported_for_positive_objectives() -> None:
    from app.engines.optimisation.mclp import Solution

    def sol(obj: float, bound: float) -> Solution:
        return Solution("feasible", obj, bound, {}, {}, {}, {}, 0.0, 0.0, "HIGHS")

    assert sol(100.0, 110.0).gap == pytest.approx(0.1)
    assert sol(-90.0, -0.5).gap is None

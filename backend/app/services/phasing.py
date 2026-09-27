"""Model C, multi-period phasing (Section 9.7): which sites to build WHEN.

Periods (default 2026 / 2028 / 2030) each have a budget. Solved period by period
(rolling horizon): each period's MCLP sees that year's unmet demand, keeps every site
built earlier open, may expand them to a larger bundle of the same class (paying only
the difference), and spends up to its own budget on expansions and new sites. The
site limit is cumulative.

This is myopic: an early period doesn't hold back budget for a later, larger gap.
The discounted coverage over the horizon is reported so alternatives can be compared
on the brief's objective (future coverage discounted), but it is not optimised
jointly.
"""

from __future__ import annotations

import uuid
from typing import Any

import numpy as np
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.planning import Optimisation, OptimisationSite, PlanningRun, Scenario
from app.services.demand_run import scenario_name
from app.services.optimisation_run import (
    _CELLS_SQL,
    _CONTEXTS,
    Context,
    OptimisationError,
    _in_solver_thread,
    _solve,
    _stats,
    build_problem,
    kpis,
    load_context,
    optimisation_config,
)

DEFAULT_PERIODS = (2026, 2028, 2030)


async def _demand_for_year(session: AsyncSession, ctx: Context, scenario: str, year: int) -> str:
    """Load d_i for (scenario, year) into the context under a year-qualified key."""
    key = f"{scenario}@{year}"
    if key in ctx.demand:
        return key
    rows = (
        await session.execute(
            _CELLS_SQL, {"region": ctx.region_id, "scenario": scenario, "year": year}
        )
    ).all()
    if not rows:
        raise OptimisationError(f"No '{scenario}' demand for {year}")
    gap = {r[0]: max(float(r[1] or 0.0), 0.0) for r in rows}
    ctx.demand[key] = np.array([gap.get(h, 0.0) for h in ctx.cells])
    ctx.region_gap[key] = float(sum(gap.values()))
    return key


async def phase_run(
    session: AsyncSession,
    run_id: uuid.UUID,
    periods: list[dict[str, Any]],
    max_sites: int | None = None,
    discount_rate: float = 0.12,
) -> Optimisation:
    if not periods or len(periods) > 6:
        raise OptimisationError("Give 1 to 6 periods")
    years = [int(p["year"]) for p in periods]
    if years != sorted(set(years)):
        raise OptimisationError("Period years must be increasing and distinct")
    run = await session.get(PlanningRun, run_id)
    if run is None:
        raise OptimisationError("Run not found")
    scenario = await session.get(Scenario, run.scenario_id)
    assert scenario is not None
    ctx = _CONTEXTS.get(run_id)
    if ctx is None:
        ctx = await load_context(session, run, scenario)
        _CONTEXTS.clear()
        _CONTEXTS[run_id] = ctx
    cfg = optimisation_config()
    demand_scenario = scenario_name(
        scenario.adoption_case, scenario.adoption_multiplier, scenario.region_id
    )
    n_max = int(max_sites or ctx.max_sites)
    limit = float(cfg.get("phasing_time_limit_s", cfg["time_limit_s"]))
    existing: dict[int, int] = {}
    opened_in: dict[int, int] = {}
    bundle_by_year: dict[int, dict[int, str]] = {}
    period_kpis: list[dict[str, Any]] = []
    solves: list[dict[str, Any]] = []
    discounted = 0.0
    last = None
    for p in periods:
        year, budget = int(p["year"]), float(p["budget_inr"])
        if budget < 0:
            raise OptimisationError("Budgets can't be negative")
        key = await _demand_for_year(session, ctx, demand_scenario, year)
        problem = build_problem(ctx, key, budget, n_max)
        problem.existing = dict(existing)
        sol = await _in_solver_thread(_solve, problem, cfg, limit)
        if sol.objective is None:
            raise OptimisationError(f"{year}: optimisation {sol.status}")
        spent = sum(problem.period_cost(j, k) for j, k in sol.open_bundle.items())
        new = [j for j in sol.open_bundle if j not in existing]
        expanded = [j for j, k in sol.open_bundle.items() if j in existing and existing[j] != k]
        for j in new:
            opened_in[j] = year
        for j, k in sol.open_bundle.items():
            bundle_by_year.setdefault(j, {})[year] = ctx.bundles[k].key
        k_year = kpis(ctx, sol, key)
        weight = 1 / (1 + discount_rate) ** (year - years[0])
        discounted += weight * float(k_year.get("coverage_kwh") or 0.0)
        period_kpis.append(
            {
                "year": year,
                "budget_inr": budget,
                "spent_inr": round(spent),
                "new_sites": len(new),
                "expanded_sites": len(expanded),
                "n_sites": len(sol.open_bundle),
                "n_charge_points": k_year.get("n_charge_points"),
                "coverage_kwh": k_year.get("coverage_kwh"),
                "demand_kwh": k_year.get("demand_kwh"),
                "coverage_share": k_year.get("coverage_share"),
                "discount_weight": round(weight, 4),
            }
        )
        solves.append({"year": year, **_stats(sol, limit)})
        existing = dict(sol.open_bundle)
        last = (sol, key)
    assert last is not None
    final, final_key = last
    opt = Optimisation(
        run_id=run_id,
        kind="phased",
        strategy="phased",
        params={
            "label": "Phased plan " + " / ".join(str(y) for y in years),
            "periods": [
                {"year": int(p["year"]), "budget_inr": float(p["budget_inr"])} for p in periods
            ],
            "max_sites": n_max,
            "discount_rate": discount_rate,
            "demand_scenario": demand_scenario,
            "method": "rolling horizon (myopic) MCLP with expansion",
            "placeholders": ctx.placeholders,
        },
        status=final.status,
        solver_stats={"periods": solves},
        kpis={
            **kpis(ctx, final, final_key),
            "cost_inr": sum(pk["spent_inr"] for pk in period_kpis),
            "periods": period_kpis,
            "discounted_coverage_kwh": round(discounted, 1),
        },
    )
    session.add(opt)
    await session.flush()
    for j, k in final.open_bundle.items():
        b = ctx.bundles[k]
        session.add(
            OptimisationSite(
                optimisation_id=opt.id,
                site_id=ctx.site_ids[j],
                bundle=b.key,
                charge_points=b.count,
                cost_inr=b.cost_inr,
                capacity_kwh=round(b.capacity_kwh, 1),
                served_kwh=round(final.served_kwh.get(j, 0.0), 1),
                served_population=round(
                    float(sum(ctx.population[i] for (i, jj) in final.served_by_cell if jj == j))
                ),
                detail={
                    "connection": b.connection,
                    "rank": ctx.ranks[j],
                    "phase_year": opened_in[j],
                    "bundle_by_year": {str(y): v for y, v in bundle_by_year[j].items()},
                },
            )
        )
    await session.commit()
    return opt

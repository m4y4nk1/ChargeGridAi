"""Optimisation step of a planning run (Section 9.7) and the what-if fast path.

The run's scores and catchments (Phase 5) are loaded once into a `Context`: the
candidate pool, the cells their catchments cover, charger bundles and costs.
From it this module builds and solves Model A for:

- the scenario's **base plan** (its budget, site limit and weight profile),
- **why-not** re-solves for strong candidates the plan left out,
- the module C **strategies** and the **Pareto** grid (strategies x budget shares),
- **what-ifs** (budget, site limit, adoption multiplier) against the base plan.

Solves are CPU-bound; callers on an event loop run them in a thread.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.assumptions import Assumptions, load_config
from app.db.models.planning import (
    Optimisation,
    OptimisationSite,
    PlanningRun,
    RunSite,
    Scenario,
)
from app.db.repositories.demand import supply_assumptions
from app.engines.optimisation.mclp import (
    Bundle,
    CostAssumptions,
    Problem,
    Solution,
    bundle,
    deciding_factor,
    solve_best,
    why_not,
)
from app.engines.scoring import score_matrix, totals
from app.services.demand_run import scenario_name
from app.services.scoring_run import scoring_config, weight_profiles

Progress = Callable[..., Awaitable[None]]

# Every solve runs on this one thread. HiGHS allocates a few hundred MB per model and
# glibc keeps a heap arena per thread, so spreading solves over the default executor's
# threads grew the worker past Docker's memory limit.
_SOLVER_THREAD = ThreadPoolExecutor(max_workers=1, thread_name_prefix="solver")


async def _in_solver_thread(fn: Callable[..., Solution], *args: Any) -> Solution:
    return await asyncio.get_running_loop().run_in_executor(_SOLVER_THREAD, fn, *args)


class OptimisationError(ValueError):
    pass


def optimisation_config() -> dict[str, Any]:
    return load_config("optimisation.yaml")


# --- context -----------------------------------------------------------------------------


@dataclass
class Context:
    run_id: uuid.UUID
    region_id: str
    target_year: int
    adoption_case: str
    adoption_multiplier: float
    budget_inr: float | None
    max_sites: int
    weight_profile: str
    site_ids: list[uuid.UUID]
    names: list[str | None]
    host_types: list[str]
    ranks: list[int]
    sub_scores: list[dict[str, float]]
    site_cells: list[list[int]]
    cells: list[str]
    population: np.ndarray
    underserved: np.ndarray  # bool per cell
    bundles: list[Bundle]
    spacing_pairs: list[tuple[int, int]]
    placeholders: list[str]
    region_underserved_population: float
    demand: dict[str, np.ndarray] = field(default_factory=dict)  # scenario -> d_i
    region_gap: dict[str, float] = field(default_factory=dict)  # scenario -> total gap

    def site_value(self, profile: str | None) -> list[float]:
        """v_j = floor + (1 - floor) * total score / 100 under `profile`."""
        cfg = optimisation_config()["value"]
        floor = float(cfg["score_floor"])
        weights = weight_profiles()[profile or self.weight_profile]["weights"]
        total = totals(score_matrix(self.sub_scores), weights)
        return [floor + (1 - floor) * float(t) / 100 for t in total]


def _costs(a: Assumptions) -> CostAssumptions:
    capex = load_config("costs", "unit_costs.yaml")["capex"]

    def g(key: str) -> float:
        return a.get(capex[key], f"costs.capex.{key}")

    classes = {
        "AC_7": "charger_ac_7kw_inr",
        "AC_22": "charger_ac_22kw_inr",
        "DC_60": "charger_dc_60kw_inr",
        "DC_120": "charger_dc_120kw_inr",
        "DC_180": "charger_dc_180kw_inr",
        "DC_240": "charger_dc_240kw_inr",
        "DC_350": "charger_dc_350kw_inr",
    }
    return CostAssumptions(
        charger_inr={c: g(k) for c, k in classes.items()},
        installation_pct=g("installation_pct_of_equipment"),
        civil_inr=g("civil_works_inr_per_site"),
        canopy_inr=g("canopy_inr_per_site"),
        software_inr=g("software_networking_inr_per_site"),
        lt_connection_inr=g("lt_connection_inr"),
        ht_connection_inr=g("ht_connection_inr"),
        transformer_inr=g("transformer_inr"),
        lt_max_sanctioned_kw=g("lt_max_sanctioned_kw"),
        diversity_factor=g("diversity_factor"),
        contingency_pct=g("contingency_pct"),
    )


def _spacing_pairs(lat: np.ndarray, lng: np.ndarray, min_m: float) -> list[tuple[int, int]]:
    la, lo = np.radians(lat), np.radians(lng)
    dlat = la[:, None] - la[None, :]
    dlng = lo[:, None] - lo[None, :]
    h = np.sin(dlat / 2) ** 2 + np.cos(la[:, None]) * np.cos(la[None, :]) * np.sin(dlng / 2) ** 2
    dist = 2 * 6_371_000 * np.arcsin(np.sqrt(h))
    a, b = np.where(np.triu(dist < min_m, k=1))
    return list(zip(a.tolist(), b.tolist(), strict=True))


_POOL_SQL = text(
    "select rs.site_id, rs.rank, rs.sub_scores, s.name, s.host_type, s.origin, "
    "ST_Y(s.geom) as lat, ST_X(s.geom) as lng, c.h3_cells "
    "from run_site rs join candidate_site s on s.id = rs.site_id "
    "join site_catchment c on c.site_id = rs.site_id "
    "where rs.run_id = :run and rs.score_total is not null "
    "and (rs.rank <= :pool or s.origin = 'user') order by rs.rank"
)

_CELLS_SQL = text(
    "select f.h3::text, f.gap_kwh, f.access_ratio, o.population "
    "from h3_cell_feature f join region_cell c on c.h3 = f.h3 "
    "left join h3_cell_feature o on o.h3 = f.h3 and o.scenario = 'observed' "
    "where c.region_id = :region and f.scenario = :scenario and f.year = :year"
)


async def load_context(session: AsyncSession, run: PlanningRun, scenario: Scenario) -> Context:
    cfg = optimisation_config()
    a = Assumptions()
    scoring = run.inputs.get("scoring")
    if not scoring:
        raise OptimisationError("This run has no scores; optimisation needs Phase 5 scoring")
    rows = (
        (await session.execute(_POOL_SQL, {"run": run.id, "pool": int(cfg["candidate_pool"])}))
        .mappings()
        .all()
    )
    if not rows:
        raise OptimisationError("No scored candidates with catchments in this run")

    demand_scenario = scenario_name(
        scenario.adoption_case, scenario.adoption_multiplier, scenario.region_id
    )
    cell_rows = (
        await session.execute(
            _CELLS_SQL,
            {
                "region": scenario.region_id,
                "scenario": demand_scenario,
                "year": scenario.target_year,
            },
        )
    ).all()
    if not cell_rows:
        raise OptimisationError(
            f"No {demand_scenario} demand for {scenario.target_year}; run `make demand`"
        )
    threshold = a.get(
        scoring_config()["equity"]["underserved_access_ratio"],
        "scoring.equity.underserved_access_ratio",
    )
    index = {r[0]: i for i, r in enumerate(cell_rows)}
    population = np.array([float(r[3] or 0.0) for r in cell_rows])
    underserved = np.array([r[2] is not None and float(r[2]) < threshold for r in cell_rows])
    gap = np.array([max(float(r[1] or 0.0), 0.0) for r in cell_rows])

    supply_a, supply_placeholders = supply_assumptions()
    kwh_per_kw_day = 24.0 * supply_a.availability * supply_a.target_utilisation
    costs = _costs(a)
    bundles = [
        bundle(cls, int(n), costs, kwh_per_kw_day)
        for cls in scenario.charger_classes
        for n in cfg["bundle_counts"]
    ]
    lat = np.array([float(r["lat"]) for r in rows])
    lng = np.array([float(r["lng"]) for r in rows])
    min_spacing = a.get(cfg["min_spacing_m"], "optimisation.min_spacing_m")
    constraints = scenario.constraints or {}
    ctx = Context(
        run_id=run.id,
        region_id=scenario.region_id,
        target_year=scenario.target_year,
        adoption_case=scenario.adoption_case,
        adoption_multiplier=scenario.adoption_multiplier,
        budget_inr=float(scenario.budget_inr) if scenario.budget_inr else None,
        max_sites=int(constraints.get("max_sites") or cfg["default_max_sites"]),
        weight_profile=scoring["weight_profile"],
        site_ids=[r["site_id"] for r in rows],
        names=[r["name"] for r in rows],
        host_types=[r["host_type"] for r in rows],
        ranks=[int(r["rank"]) for r in rows],
        sub_scores=[r["sub_scores"] for r in rows],
        site_cells=[[index[h] for h in r["h3_cells"] if h in index] for r in rows],
        cells=[r[0] for r in cell_rows],
        population=population,
        underserved=underserved,
        bundles=bundles,
        spacing_pairs=_spacing_pairs(lat, lng, min_spacing),
        placeholders=sorted(set(a.placeholders_in_use) | set(supply_placeholders)),
        region_underserved_population=float(population[underserved].sum()),
    )
    ctx.demand[demand_scenario] = gap
    ctx.region_gap[demand_scenario] = float(gap.sum())
    return ctx


async def ensure_demand(session: AsyncSession, ctx: Context, demand_scenario: str) -> None:
    """Load d_i for another adoption scenario (what-ifs) on the context's cells."""
    if demand_scenario in ctx.demand:
        return
    rows = (
        await session.execute(
            _CELLS_SQL,
            {"region": ctx.region_id, "scenario": demand_scenario, "year": ctx.target_year},
        )
    ).all()
    if not rows:
        raise OptimisationError(
            f"No '{demand_scenario}' demand for {ctx.target_year}. Run "
            f"`make demand MULTIPLIER=<x>` for that adoption multiplier first."
        )
    gap = {r[0]: max(float(r[1] or 0.0), 0.0) for r in rows}
    ctx.demand[demand_scenario] = np.array([gap.get(h, 0.0) for h in ctx.cells])
    ctx.region_gap[demand_scenario] = float(sum(gap.values()))


# --- problems ----------------------------------------------------------------------------


def build_problem(
    ctx: Context,
    demand_scenario: str,
    budget_inr: float,
    max_sites: int,
    strategy: dict[str, Any] | None = None,
    forced_open: frozenset[int] = frozenset(),
    hint: dict[int, int] | None = None,
    min_served_kwh: float = 0.0,
) -> Problem:
    """strategy None = the scenario's base plan: kWh weighted by its own profile's scores."""
    cfg = optimisation_config()
    lam = float(cfg["value"]["lambda_per_inr"])
    s = strategy or {
        "value_by": "score",
        "kwh_weight": 1.0,
        "population_weight": 0.0,
        "lambda_multiplier": 1,
    }
    value = (
        ctx.site_value(s.get("profile")) if s["value_by"] == "score" else [1.0] * len(ctx.site_ids)
    )
    demand = ctx.demand[demand_scenario]
    weight = ctx.population * ctx.underserved
    beta = 0.0
    if s["population_weight"] > 0 and weight.sum() > 0:
        # Covering every underserved person in the region = the region's whole gap in kWh.
        beta = float(s["population_weight"]) * ctx.region_gap[demand_scenario] / float(weight.sum())
    lam_value = lam * float(s.get("lambda_multiplier", 1))
    return Problem(
        site_ids=[str(i) for i in ctx.site_ids],
        site_value=value,
        site_cells=ctx.site_cells,
        bundles=ctx.bundles,
        demand=demand.tolist(),
        coverage_weight=weight.tolist(),
        budget_inr=budget_inr,
        max_sites=max_sites,
        spacing_pairs=ctx.spacing_pairs,
        alpha=float(s["kwh_weight"]),
        beta=beta,
        lambda_per_inr=lam_value,
        forced_open=forced_open,
        hint=hint or {},
        min_served_kwh=min_served_kwh,
    )


def kpis(ctx: Context, sol: Solution, demand_scenario: str) -> dict[str, Any]:
    covered = np.zeros(len(ctx.cells), dtype=bool)
    for j in sol.open_bundle:
        covered[ctx.site_cells[j]] = True
    capacity = sum(ctx.bundles[k].capacity_kwh for k in sol.open_bundle.values())
    mix: Counter[str] = Counter()
    for k in sol.open_bundle.values():
        mix[ctx.bundles[k].charger_class] += ctx.bundles[k].count
    region_gap = ctx.region_gap[demand_scenario]
    top = sorted(sol.served_kwh.items(), key=lambda kv: -kv[1])[:5]
    return {
        "coverage_kwh": round(sol.total_served_kwh, 1),
        "demand_kwh": round(region_gap, 1),
        "coverage_share": round(sol.total_served_kwh / region_gap, 4) if region_gap else None,
        "cost_inr": round(sol.cost_inr),
        "n_sites": len(sol.open_bundle),
        "n_charge_points": sum(mix.values()),
        "charger_mix": dict(mix),
        "utilisation": round(sol.total_served_kwh / capacity, 4) if capacity else None,
        # Share of the region's underserved population inside an open site's catchment.
        "equity": (
            round(
                float(ctx.population[covered & ctx.underserved].sum())
                / ctx.region_underserved_population,
                4,
            )
            if ctx.region_underserved_population
            else None
        ),
        "population_in_catchments": round(float(ctx.population[covered].sum())),
        "top_sites": [
            {
                "site_id": str(ctx.site_ids[j]),
                "name": ctx.names[j],
                "host_type": ctx.host_types[j],
                "served_kwh": round(kwh, 1),
            }
            for j, kwh in top
        ],
    }


def _stats(sol: Solution, time_limit_s: float) -> dict[str, Any]:
    return {
        "solver": sol.solver,
        "status": sol.status,
        "objective": sol.objective,
        "bound": sol.bound,
        "mip_gap": sol.gap,
        "solve_s": round(sol.solve_s, 3),
        "time_limit_s": time_limit_s,
    }


def _cell_assignment(ctx: Context, sol: Solution) -> dict[str, Any]:
    best: dict[int, tuple[int, float]] = {}
    for (i, j), kwh in sol.served_by_cell.items():
        if kwh > best.get(i, (-1, 0.0))[1]:
            best[i] = (j, kwh)
    return {
        ctx.cells[i]: {"site_id": str(ctx.site_ids[j]), "served_kwh": round(kwh, 2)}
        for i, (j, kwh) in best.items()
    }


async def save_solution(
    session: AsyncSession,
    ctx: Context,
    sol: Solution,
    kind: str,
    strategy: str,
    params: dict[str, Any],
    demand_scenario: str,
    time_limit_s: float,
    with_cells: bool,
) -> Optimisation:
    opt = Optimisation(
        run_id=ctx.run_id,
        kind=kind,
        strategy=strategy,
        params={**params, "demand_scenario": demand_scenario, "placeholders": ctx.placeholders},
        status=sol.status,
        solver_stats=_stats(sol, time_limit_s),
        kpis=kpis(ctx, sol, demand_scenario) if sol.objective is not None else {},
        cell_assignment=_cell_assignment(ctx, sol) if with_cells else None,
    )
    session.add(opt)
    await session.flush()
    for j, k in sol.open_bundle.items():
        b = ctx.bundles[k]
        served_pop = float(sum(ctx.population[i] for (i, jj) in sol.served_by_cell if jj == j))
        session.add(
            OptimisationSite(
                optimisation_id=opt.id,
                site_id=ctx.site_ids[j],
                bundle=b.key,
                charge_points=b.count,
                cost_inr=b.cost_inr,
                capacity_kwh=round(b.capacity_kwh, 1),
                served_kwh=round(sol.served_kwh.get(j, 0.0), 1),
                served_population=round(served_pop),
                detail={
                    "connection": b.connection,
                    "sanctioned_kw": b.sanctioned_kw,
                    "cost_breakdown": dict(b.cost_breakdown),
                    "rank": ctx.ranks[j],
                },
            )
        )
    return opt


def _solve(p: Problem, cfg: dict[str, Any], time_limit_s: float | None = None) -> Solution:
    return solve_best(
        p,
        solver_name=str(cfg["solver"]),
        time_limit_s=float(time_limit_s or cfg["time_limit_s"]),
        mip_gap=float(cfg["mip_gap"]),
    )


# --- the run step ------------------------------------------------------------------------


def _pareto_front(points: Sequence[dict[str, Any]]) -> set[int]:
    """Indices not dominated on (coverage up, cost down, equity up)."""
    front = set()
    for i, p in enumerate(points):
        dominated = any(
            q["coverage_kwh"] >= p["coverage_kwh"]
            and q["cost_inr"] <= p["cost_inr"]
            and (q["equity"] or 0) >= (p["equity"] or 0)
            and (
                q["coverage_kwh"] > p["coverage_kwh"]
                or q["cost_inr"] < p["cost_inr"]
                or (q["equity"] or 0) > (p["equity"] or 0)
            )
            for j, q in enumerate(points)
            if j != i
        )
        if not dominated:
            front.add(i)
    return front


async def optimise_run(
    session: AsyncSession, run: PlanningRun, scenario: Scenario, progress: Progress
) -> dict[str, Any]:
    """Base plan, why-not, strategies and Pareto grid. Returns the funnel's `selected`."""
    cfg = optimisation_config()
    await progress(session, run, "optimise", "started")
    if not scenario.budget_inr:
        await progress(session, run, "optimise", "completed", skipped="no budget set")
        return {"selected": None, "optimisation": {"skipped": "set a budget to optimise"}}

    ctx = await load_context(session, run, scenario)
    demand_scenario = scenario_name(
        scenario.adoption_case, scenario.adoption_multiplier, scenario.region_id
    )
    budget, n_max = float(scenario.budget_inr), ctx.max_sites
    base_problem = build_problem(ctx, demand_scenario, budget, n_max)
    base = await _in_solver_thread(_solve, base_problem, cfg)
    if base.objective is None:
        raise OptimisationError(f"Base optimisation {base.status}")
    params = {"budget_inr": budget, "max_sites": n_max, "weight_profile": ctx.weight_profile}
    base_opt = await save_solution(
        session,
        ctx,
        base,
        "base",
        "scenario",
        params,
        demand_scenario,
        float(cfg["time_limit_s"]),
        with_cells=True,
    )
    selected = set(base.open_bundle)
    served_pop = {
        j: float(sum(ctx.population[i] for (i, jj) in base.served_by_cell if jj == j))
        for j in selected
    }
    for j in selected:
        await session.execute(
            update(RunSite)
            .where(RunSite.run_id == run.id, RunSite.site_id == ctx.site_ids[j])
            .values(
                selected=True,
                phase_year=scenario.target_year,
                coverage_kwh=round(base.served_kwh.get(j, 0.0), 1),
                served_population=round(served_pop[j]),
            )
        )
    await progress(
        session,
        run,
        "optimise",
        "completed",
        sites=len(selected),
        cost_inr=round(base.cost_inr),
        solve_s=round(base.solve_s, 2),
    )

    # --- why-not
    await progress(session, run, "why_not", "started")
    wn_cfg = cfg["why_not"]
    pool_rank = int(wn_cfg["pool_multiple"]) * n_max
    candidates = [
        j for j in range(len(ctx.site_ids)) if j not in selected and ctx.ranks[j] <= pool_rank
    ][: int(wn_cfg["max_sites"])]
    weights = weight_profiles()[ctx.weight_profile]["weights"]
    explained = 0
    for j in candidates:
        forced = await _in_solver_thread(
            _solve,
            build_problem(
                ctx,
                demand_scenario,
                budget,
                n_max,
                forced_open=frozenset({j}),
                hint=dict(base.open_bundle),
            ),
            cfg,
            float(wn_cfg["time_limit_s"]),
        )
        w = why_not(base, forced, j)
        rival = w.displaced[0] if w.displaced else None
        factor, points = (
            deciding_factor(ctx.sub_scores[j], ctx.sub_scores[rival], weights)
            if rival is not None
            else ("none", 0.0)
        )
        conflicts = [
            b if a == j else a
            for a, b in ctx.spacing_pairs
            if j in (a, b) and (b if a == j else a) in selected
        ]
        await session.execute(
            update(RunSite)
            .where(RunSite.run_id == run.id, RunSite.site_id == ctx.site_ids[j])
            .values(
                why_not={
                    "status": w.status,
                    "objective_delta": round(w.objective_delta, 2),
                    "served_delta_kwh": round(w.served_delta_kwh, 1),
                    "cost_delta_inr": round(w.cost_delta_inr),
                    "displaced": [
                        {
                            "site_id": str(ctx.site_ids[d]),
                            "name": ctx.names[d],
                            "rank": ctx.ranks[d],
                        }
                        for d in w.displaced
                    ],
                    "spacing_conflicts": [
                        {"site_id": str(ctx.site_ids[c]), "name": ctx.names[c]} for c in conflicts
                    ],
                    "deciding_sub_score": factor,
                    "deciding_points": round(points, 2),
                    "solve_s": round(forced.solve_s, 2),
                }
            )
        )
        explained += 1
    await progress(session, run, "why_not", "completed", explained=explained)

    # --- strategies and Pareto grid
    await progress(session, run, "strategies", "started")
    grid: list[tuple[str, float, Solution]] = []
    served_by: dict[tuple[str, float], float] = {}
    for name, s in cfg["strategies"].items():
        for share in cfg["pareto_budget_shares"]:
            floor = 0.0
            if "coverage_of" in s:
                # Needs its reference strategy solved first at this budget (config order).
                floor = float(s["coverage_share"]) * served_by.get((s["coverage_of"], share), 0.0)
            sol = await _in_solver_thread(
                _solve,
                build_problem(
                    ctx,
                    demand_scenario,
                    budget * float(share),
                    n_max,
                    strategy=s,
                    min_served_kwh=floor,
                ),
                cfg,
                float(cfg["pareto_time_limit_s"] if share != 1.0 else cfg["whatif_time_limit_s"]),
            )
            if sol.objective is not None:
                grid.append((name, float(share), sol))
                served_by[(name, share)] = sol.total_served_kwh
    opts = []
    for name, share, sol in grid:
        opts.append(
            await save_solution(
                session,
                ctx,
                sol,
                "strategy" if share == 1.0 else "pareto",
                name,
                {
                    "budget_inr": budget * share,
                    "budget_share": share,
                    "max_sites": n_max,
                    "label": cfg["strategies"][name]["label"],
                },
                demand_scenario,
                float(cfg["whatif_time_limit_s"] if share == 1.0 else cfg["pareto_time_limit_s"]),
                with_cells=share == 1.0,
            )
        )
    for i in _pareto_front([o.kpis for o in opts]):
        opts[i].on_front = True
    await progress(session, run, "strategies", "completed", solves=len(grid))

    k = base_opt.kpis
    return {
        "selected": len(selected),
        "optimisation": {
            "base_id": str(base_opt.id),
            "cost_inr": k["cost_inr"],
            "budget_inr": budget,
            "coverage_share": k["coverage_share"],
            "n_charge_points": k["n_charge_points"],
        },
    }


# --- what-if fast path -----------------------------------------------------------------------

_CONTEXTS: dict[uuid.UUID, Context] = {}


async def whatif(
    session: AsyncSession,
    run_id: uuid.UUID,
    budget_inr: float | None,
    max_sites: int | None,
    adoption_multiplier: float | None,
) -> Optimisation:
    """Re-solve the base plan with changed inputs and diff it (target < 10 s)."""
    started = time.perf_counter()
    cfg = optimisation_config()
    run = await session.get(PlanningRun, run_id)
    if run is None:
        raise OptimisationError("Run not found")
    scenario = await session.get(Scenario, run.scenario_id)
    assert scenario is not None
    base_opt = (
        await session.execute(
            select(Optimisation).where(Optimisation.run_id == run_id, Optimisation.kind == "base")
        )
    ).scalar_one_or_none()
    if base_opt is None:
        raise OptimisationError("This run has no base plan to compare against (set a budget)")
    ctx = _CONTEXTS.get(run_id)
    if ctx is None:
        ctx = await load_context(session, run, scenario)
        _CONTEXTS.clear()  # keep one run's context in memory
        _CONTEXTS[run_id] = ctx
    mult = adoption_multiplier or scenario.adoption_multiplier
    demand_scenario = scenario_name(scenario.adoption_case, mult, scenario.region_id)
    await ensure_demand(session, ctx, demand_scenario)
    loaded = time.perf_counter()

    base_sites = {
        str(sid): b
        for sid, b in (
            await session.execute(
                select(OptimisationSite.site_id, OptimisationSite.bundle).where(
                    OptimisationSite.optimisation_id == base_opt.id
                )
            )
        ).all()
    }
    index = {str(s): j for j, s in enumerate(ctx.site_ids)}
    keys = [b.key for b in ctx.bundles]
    hint = {index[s]: keys.index(b) for s, b in base_sites.items() if s in index and b in keys}
    budget = float(budget_inr or base_opt.params["budget_inr"])
    n_max = int(max_sites or base_opt.params["max_sites"])
    sol = await _in_solver_thread(
        _solve,
        build_problem(ctx, demand_scenario, budget, n_max, hint=hint),
        cfg,
        float(cfg["whatif_time_limit_s"]),
    )
    if sol.objective is None:
        raise OptimisationError(f"What-if optimisation {sol.status}")
    params = {
        "budget_inr": budget,
        "max_sites": n_max,
        "adoption_multiplier": mult,
        "weight_profile": ctx.weight_profile,
    }
    opt = await save_solution(
        session,
        ctx,
        sol,
        "whatif",
        "scenario",
        params,
        demand_scenario,
        float(cfg["whatif_time_limit_s"]),
        with_cells=True,
    )
    new_sites = {str(ctx.site_ids[j]): ctx.bundles[k].key for j, k in sol.open_bundle.items()}
    names = dict(zip((str(s) for s in ctx.site_ids), ctx.names, strict=True))

    def entry(sid: str, **extra: Any) -> dict[str, Any]:
        return {"site_id": sid, "name": names.get(sid), **extra}

    base_k = base_opt.kpis
    opt.diff = {
        "base_id": str(base_opt.id),
        "added": [entry(s, bundle=b) for s, b in new_sites.items() if s not in base_sites],
        "removed": [entry(s, bundle=b) for s, b in base_sites.items() if s not in new_sites],
        "resized": [
            entry(s, before=base_sites[s], after=b)
            for s, b in new_sites.items()
            if s in base_sites and base_sites[s] != b
        ],
        "kept": sum(1 for s in new_sites if s in base_sites),
        "delta": {
            k: (opt.kpis.get(k) or 0) - (base_k.get(k) or 0)
            for k in ("coverage_kwh", "cost_inr", "n_sites", "n_charge_points", "equity")
        },
    }
    finished = time.perf_counter()
    opt.solver_stats = {
        **opt.solver_stats,
        "load_s": round(loaded - started, 3),
        "total_s": round(finished - started, 3),
        "context_cached": run_id in _CONTEXTS and loaded - started < 0.5,
    }
    await session.commit()
    return opt

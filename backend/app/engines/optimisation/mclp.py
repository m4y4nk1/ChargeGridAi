"""Optimisation engine (Section 9.7), Model A: budget-constrained capacitated maximal
covering (MCLP). Pure: a `Problem` in, a `Solution` out, solved with OR-Tools.

Sets: demand cells i, candidate sites j, charger bundles k (n chargers of a class).
Variables: x_j open, z_jk bundle, y_ij >= 0 kWh/day of cell i's unmet demand served
by site j (only where i lies in j's drive-time catchment), c_i in [0, 1] share of
cell i's population covered by some open site (only when population coverage is
valued).

    maximise  alpha * sum v_j * y_ij  +  beta * sum p_i * c_i  -  lambda * sum c_jk * z_jk
    s.t.      sum_k z_jk = x_j                       one bundle per open site
              sum_i y_ij <= sum_k cap_k * z_jk        site capacity
              sum_j y_ij <= d_i                       cell demand
              c_i <= sum_{j covers i} x_j             coverage needs an open site
              sum c_jk z_jk <= budget;  sum x_j <= N_max
              x_j + x_j' <= 1 for pairs closer than the minimum spacing
              x_j = 1 (forced, why-not);  x_j = 0 (excluded)
"""

from __future__ import annotations

import math
import time
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace

from ortools.linear_solver import pywraplp

# --- costs -----------------------------------------------------------------------------


@dataclass(frozen=True)
class CostAssumptions:
    charger_inr: Mapping[str, float]  # per charger, by class (e.g. DC_60)
    installation_pct: float
    civil_inr: float
    canopy_inr: float
    software_inr: float
    lt_connection_inr: float
    ht_connection_inr: float
    transformer_inr: float
    lt_max_sanctioned_kw: float
    diversity_factor: float
    contingency_pct: float


def charger_kw(charger_class: str) -> float:
    """DC_60 -> 60.0, AC_22 -> 22.0."""
    return float(charger_class.split("_", 1)[1])


@dataclass(frozen=True)
class Bundle:
    key: str  # e.g. DC_60x4
    charger_class: str
    count: int
    kw: float
    sanctioned_kw: float
    connection: str  # LT | HT
    cost_inr: float
    cost_breakdown: Mapping[str, float]
    capacity_kwh: float  # deliverable kWh/day at the target utilisation


def bundle(
    charger_class: str,
    count: int,
    costs: CostAssumptions,
    kwh_per_kw_day: float,
) -> Bundle:
    """Site CAPEX and daily capacity for `count` chargers of one class.

    kwh_per_kw_day = 24 h x availability x target utilisation (Phase 3 supply
    assumptions), so optimisation and the gap model agree on what a charger delivers.
    """
    kw = charger_kw(charger_class) * count
    sanctioned = kw * costs.diversity_factor
    ht = sanctioned > costs.lt_max_sanctioned_kw
    equipment = costs.charger_inr[charger_class] * count
    lines = {
        "chargers": equipment,
        "installation": equipment * costs.installation_pct,
        "civil": costs.civil_inr,
        "canopy": costs.canopy_inr,
        "software_networking": costs.software_inr,
        "connection": costs.ht_connection_inr if ht else costs.lt_connection_inr,
        "transformer": costs.transformer_inr if ht else 0.0,
    }
    subtotal = sum(lines.values())
    lines["contingency"] = subtotal * costs.contingency_pct
    return Bundle(
        key=f"{charger_class}x{count}",
        charger_class=charger_class,
        count=count,
        kw=kw,
        sanctioned_kw=round(sanctioned, 1),
        connection="HT" if ht else "LT",
        cost_inr=round(sum(lines.values())),
        cost_breakdown={k: round(v) for k, v in lines.items()},
        capacity_kwh=kw * kwh_per_kw_day,
    )


# --- model -----------------------------------------------------------------------------


@dataclass
class Problem:
    site_ids: Sequence[str]
    site_value: Sequence[float]  # v_j, value of one kWh/day served by site j
    site_cells: Sequence[Sequence[int]]  # cell indices each site's catchment covers
    bundles: Sequence[Bundle]
    demand: Sequence[float]  # d_i, unmet kWh/day per cell
    coverage_weight: Sequence[float]  # p_i, population weight for the coverage term
    budget_inr: float
    max_sites: int
    spacing_pairs: Sequence[tuple[int, int]] = ()
    alpha: float = 1.0
    beta: float = 0.0
    lambda_per_inr: float = 0.0
    forced_open: frozenset[int] = frozenset()
    excluded: frozenset[int] = frozenset()
    hint: Mapping[int, int] = field(default_factory=dict)  # site -> bundle index
    # Serve at least this many kWh/day in total (the cost-optimised strategy).
    min_served_kwh: float = 0.0
    # Model C (phasing): sites built in an earlier period (site -> bundle). They stay
    # open; they may be expanded to a larger bundle of the same charger class, and only
    # the extra cost counts against this period's budget.
    existing: Mapping[int, int] = field(default_factory=dict)

    def allowed(self, j: int, k: int) -> bool:
        if j not in self.existing:
            return True
        old = self.bundles[self.existing[j]]
        new = self.bundles[k]
        return new.charger_class == old.charger_class and new.count >= old.count

    def period_cost(self, j: int, k: int) -> float:
        """Cost of site j running bundle k, net of what was already built there."""
        cost = self.bundles[k].cost_inr
        return cost - self.bundles[self.existing[j]].cost_inr if j in self.existing else cost


@dataclass
class Solution:
    status: str  # optimal | feasible | infeasible | not_solved
    objective: float | None
    bound: float | None
    open_bundle: dict[int, int]  # site index -> bundle index
    served_kwh: dict[int, float]  # site index -> kWh/day
    served_by_cell: dict[tuple[int, int], float]  # (cell, site) -> kWh/day
    covered: dict[int, float]  # cell -> covered share (only when beta > 0)
    cost_inr: float
    solve_s: float
    solver: str

    @property
    def total_served_kwh(self) -> float:
        return sum(self.served_kwh.values())

    @property
    def gap(self) -> float | None:
        # A relative gap only means something against a positive objective; the
        # cost-minimising strategy's objective is about minus its cost.
        if self.objective is None or self.bound is None or self.objective <= 0:
            return None
        return abs(self.bound - self.objective) / max(abs(self.objective), 1e-9)


_STATUS = {
    pywraplp.Solver.OPTIMAL: "optimal",
    pywraplp.Solver.FEASIBLE: "feasible",
    pywraplp.Solver.INFEASIBLE: "infeasible",
}


def _zones(p: Problem) -> list[tuple[tuple[int, ...], list[int]]]:
    """Cells covered by exactly the same set of sites are interchangeable, so they are
    merged into zones (lossless) and split back pro rata to demand afterwards."""
    covering: dict[int, list[int]] = defaultdict(list)
    for j, cells in enumerate(p.site_cells):
        for i in cells:
            covering[i].append(j)
    zones: dict[tuple[int, ...], list[int]] = defaultdict(list)
    for i, sites in covering.items():
        if p.demand[i] > 0 or (p.beta > 0 and p.coverage_weight[i] > 0):
            zones[tuple(sorted(sites))].append(i)
    return list(zones.items())


def solve(
    p: Problem,
    solver_name: str = "HIGHS",
    time_limit_s: float = 8.0,
    mip_gap: float = 0.01,
    fixed: Mapping[int, int] | None = None,
) -> Solution:
    """MIP solve. With `fixed` (site -> bundle), every x/z is fixed and only the demand
    allocation is optimised: an exact evaluation of a given plan."""
    start = time.perf_counter()
    solver = pywraplp.Solver.CreateSolver(solver_name)
    if solver is None:
        raise RuntimeError(f"OR-Tools solver {solver_name} is not available")
    solver.SetTimeLimit(int(time_limit_s * 1000))
    params = pywraplp.MPSolverParameters()
    params.SetDoubleParam(pywraplp.MPSolverParameters.RELATIVE_MIP_GAP, mip_gap)

    # Built with the low-level API (coefficients set on constraints and the objective,
    # unnamed variables): Python expression trees over ~40k terms cost hundreds of MB.
    inf = solver.infinity()
    n_sites, n_bundles = len(p.site_ids), len(p.bundles)
    x = [solver.BoolVar("") for _ in range(n_sites)]
    z = [[solver.BoolVar("") for _ in range(n_bundles)] for _ in range(n_sites)]
    obj = solver.Objective()
    budget = solver.Constraint(-inf, p.budget_inr)
    site_limit = solver.Constraint(-inf, float(p.max_sites))
    capacity = []
    for j in range(n_sites):
        one_bundle = solver.Constraint(0.0, 0.0)  # sum_k z_jk - x_j = 0
        one_bundle.SetCoefficient(x[j], -1.0)
        cap = solver.Constraint(-inf, 0.0)  # sum_i y_ij - sum_k cap_k z_jk <= 0
        for k in range(n_bundles):
            one_bundle.SetCoefficient(z[j][k], 1.0)
            cap.SetCoefficient(z[j][k], -p.bundles[k].capacity_kwh)
            budget.SetCoefficient(z[j][k], p.period_cost(j, k))
            obj.SetCoefficient(z[j][k], -p.lambda_per_inr * p.period_cost(j, k))
            if fixed is not None:
                z[j][k].SetBounds(*((1.0, 1.0) if fixed.get(j) == k else (0.0, 0.0)))
            elif not p.allowed(j, k):
                z[j][k].SetBounds(0.0, 0.0)
        capacity.append(cap)
        site_limit.SetCoefficient(x[j], 1.0)
        if j in p.forced_open or j in p.existing:
            x[j].SetBounds(1.0, 1.0)
        if j in p.excluded:
            x[j].SetBounds(0.0, 0.0)
    for a, b in p.spacing_pairs:
        pair = solver.Constraint(-inf, 1.0)
        pair.SetCoefficient(x[a], 1.0)
        pair.SetCoefficient(x[b], 1.0)

    zones = _zones(p)
    served_floor = solver.Constraint(p.min_served_kwh, inf) if p.min_served_kwh > 0 else None
    y: dict[tuple[int, int], pywraplp.Variable] = {}  # (zone, site)
    cov: dict[int, pywraplp.Variable] = {}  # zone -> covered share
    zone_cells: list[list[int]] = []
    zone_demand: list[float] = []
    for zi, (sites, cells) in enumerate(zones):
        demand = float(sum(p.demand[i] for i in cells))
        weight = float(sum(p.coverage_weight[i] for i in cells))
        zone_cells.append(cells)
        zone_demand.append(demand)
        if demand > 0:
            shared = solver.Constraint(-inf, demand) if len(sites) > 1 else None
            for j in sites:
                v = solver.NumVar(0.0, demand, "")
                y[(zi, j)] = v
                capacity[j].SetCoefficient(v, 1.0)
                obj.SetCoefficient(v, p.alpha * p.site_value[j])
                if served_floor is not None:
                    served_floor.SetCoefficient(v, 1.0)
                if shared is not None:
                    shared.SetCoefficient(v, 1.0)
        if p.beta > 0 and weight > 0:
            c = solver.NumVar(0.0, 1.0, "")
            covers = solver.Constraint(-inf, 0.0)  # c_z - sum_j x_j <= 0
            covers.SetCoefficient(c, 1.0)
            for j in sites:
                covers.SetCoefficient(x[j], -1.0)
            obj.SetCoefficient(c, p.beta * weight)
            cov[zi] = c
    obj.SetMaximization()

    # HiGHS crashes (segfault) on hints through OR-Tools 9.15, so it never gets one.
    if p.hint and solver_name != "HIGHS":
        hint_vars, hint_vals = [], []
        for j in range(n_sites):
            hint_vars.append(x[j])
            hint_vals.append(1.0 if j in p.hint else 0.0)
            for k in range(n_bundles):
                hint_vars.append(z[j][k])
                hint_vals.append(1.0 if p.hint.get(j) == k else 0.0)
        solver.SetHint(hint_vars, hint_vals)

    status = _STATUS.get(solver.Solve(params), "not_solved")
    elapsed = time.perf_counter() - start
    if status not in ("optimal", "feasible"):
        # A timed-out search may still have proved a bound; keep it when it is finite.
        bound = solver.Objective().BestBound() if status == "not_solved" else None
        if bound is not None and not math.isfinite(bound):
            bound = None
        return Solution(status, None, bound, {}, {}, {}, {}, 0.0, elapsed, solver_name)

    open_bundle = {
        j: k for j in range(n_sites) for k in range(n_bundles) if z[j][k].solution_value() > 0.5
    }
    served: dict[int, float] = {j: 0.0 for j in open_bundle}
    served_by_cell: dict[tuple[int, int], float] = {}
    for (zi, j), v in y.items():
        kwh = v.solution_value()
        if kwh <= 1e-6:
            continue
        served[j] = served.get(j, 0.0) + kwh
        for i in zone_cells[zi]:
            share = kwh * p.demand[i] / zone_demand[zi]
            if share > 1e-6:
                served_by_cell[(i, j)] = served_by_cell.get((i, j), 0.0) + share
    covered = {
        i: v.solution_value()
        for zi, v in cov.items()
        if v.solution_value() > 1e-6
        for i in zone_cells[zi]
        if p.coverage_weight[i] > 0
    }
    return Solution(
        status=status,
        objective=solver.Objective().Value(),
        bound=solver.Objective().BestBound(),
        open_bundle=open_bundle,
        served_kwh=served,
        served_by_cell=served_by_cell,
        covered=covered,
        cost_inr=float(sum(p.bundles[k].cost_inr for k in open_bundle.values())),
        solve_s=elapsed,
        solver=solver_name,
    )


# --- heuristic + race -------------------------------------------------------------------


def greedy(p: Problem) -> dict[int, int]:
    """Add the (site, bundle) with the best marginal value per rupee until nothing fits.

    Marginal kWh = min(capacity, the site's catchment demand not yet taken); demand is
    taken from the least-contested zones first. Respects budget, site limit, spacing,
    forced and excluded sites. Returns site -> bundle.
    """
    zones = _zones(p)
    n_sites = len(p.site_ids)
    remaining = [float(sum(p.demand[i] for i in cells)) for _, cells in zones]
    weight = [float(sum(p.coverage_weight[i] for i in cells)) for _, cells in zones]
    covered = [False] * len(zones)
    site_zones: list[list[int]] = [[] for _ in range(n_sites)]
    for zi, (sites, _) in enumerate(zones):
        for j in sites:
            site_zones[j].append(zi)
    for zs in site_zones:
        zs.sort(key=lambda zi: len(zones[zi][0]))  # least contested first
    conflicts: dict[int, set[int]] = defaultdict(set)
    for a, b in p.spacing_pairs:
        conflicts[a].add(b)
        conflicts[b].add(a)

    chosen: dict[int, int] = {}
    budget = p.budget_inr

    def take(j: int, cap: float) -> None:
        for zi in site_zones[j]:
            if cap <= 0:
                break
            got = min(cap, remaining[zi])
            remaining[zi] -= got
            cap -= got
        for zi in site_zones[j]:
            covered[zi] = True

    for j, k in sorted(p.existing.items()):
        chosen[j] = k
        take(j, p.bundles[k].capacity_kwh)

    for j in sorted(p.forced_open - set(p.existing)):
        k = min(range(len(p.bundles)), key=lambda k: p.bundles[k].cost_inr)
        chosen[j] = k
        budget -= p.bundles[k].cost_inr
        take(j, p.bundles[k].capacity_kwh)

    served_total = 0.0
    while len(chosen) < p.max_sites:
        # With a service floor still unmet, buy the cheapest kWh/day first, whatever the
        # objective says; once it is met, only add what the objective values.
        chasing_floor = served_total < p.min_served_kwh
        best: tuple[float, int, int] | None = None
        for j in range(n_sites):
            if j in chosen or j in p.excluded or conflicts[j] & chosen.keys():
                continue
            avail = sum(remaining[zi] for zi in site_zones[j])
            new_cover = sum(weight[zi] for zi in site_zones[j] if not covered[zi])
            for k, option in enumerate(p.bundles):
                if option.cost_inr > budget:
                    continue
                gain = (
                    p.alpha * p.site_value[j] * min(option.capacity_kwh, avail)
                    + p.beta * new_cover
                    - p.lambda_per_inr * option.cost_inr
                )
                if chasing_floor:
                    gain = min(option.capacity_kwh, avail)
                if gain <= 0:
                    continue
                ratio = gain / option.cost_inr
                if best is None or ratio > best[0]:
                    best = (ratio, j, k)
        if best is None:
            break
        _, j, k = best
        chosen[j] = k
        budget -= p.bundles[k].cost_inr
        served_total += min(p.bundles[k].capacity_kwh, sum(remaining[zi] for zi in site_zones[j]))
        take(j, p.bundles[k].capacity_kwh)
    return chosen


def evaluate(p: Problem, plan: Mapping[int, int], solver_name: str = "HIGHS") -> Solution:
    """Exact value of a fixed plan (site -> bundle): the optimal demand allocation.

    Solved on the plan's own sites only (an LP of a few hundred variables), then mapped
    back to the full problem's indices. Same objective as fixing x/z in the full model:
    closed sites neither serve nor cover.
    """
    keep = sorted(plan)
    sub = replace(
        p,
        site_ids=[p.site_ids[j] for j in keep],
        site_value=[p.site_value[j] for j in keep],
        site_cells=[p.site_cells[j] for j in keep],
        budget_inr=float("inf"),
        max_sites=len(keep),
        spacing_pairs=(),
        forced_open=frozenset(),
        excluded=frozenset(),
        hint={},
        existing={},
    )
    s = solve(sub, solver_name, 30.0, 0.0, fixed={n: plan[j] for n, j in enumerate(keep)})
    # The sub-problem charges existing sites their full cost; this period only pays
    # the expansion, so add the already-built part back to stay comparable with solve().
    sunk = p.lambda_per_inr * sum(p.bundles[k].cost_inr for j, k in p.existing.items() if j in plan)
    return replace(
        s,
        objective=None if s.objective is None else s.objective + sunk,
        bound=None if s.bound is None else s.bound + sunk,
        open_bundle={keep[n]: k for n, k in s.open_bundle.items()},
        served_kwh={keep[n]: v for n, v in s.served_kwh.items()},
        served_by_cell={(i, keep[n]): v for (i, n), v in s.served_by_cell.items()},
    )


def solve_best(
    p: Problem, solver_name: str = "HIGHS", time_limit_s: float = 8.0, mip_gap: float = 0.01
) -> Solution:
    """Greedy plan (evaluated exactly) raced against the MIP within the time limit.

    Returns the better of the two. Status is the MIP's when it wins or proves the
    greedy plan within the gap; `heuristic` when only the greedy plan is available.
    The MIP's best bound is kept, so the reported gap is always honest.
    """
    start = time.perf_counter()
    plan = greedy(p)
    heuristic = evaluate(p, plan, solver_name) if plan else None
    left = max(time_limit_s - (time.perf_counter() - start), 1.0)
    mip = solve(p, solver_name, left, mip_gap)
    elapsed = time.perf_counter() - start
    candidates = [s for s in (mip, heuristic) if s is not None and s.objective is not None]
    if not candidates:
        mip.solve_s = elapsed
        return mip
    best = max(candidates, key=lambda s: s.objective or 0.0)
    bound = mip.bound
    # A valid upper bound can't sit below a feasible objective; a timed-out search that
    # never finished its root LP can report one, so treat it as no bound at all.
    if bound is not None and best.objective is not None:
        if bound < best.objective - 1e-6 * max(abs(best.objective), 1.0):
            bound = None
    status = mip.status if best is mip else ("feasible" if bound is not None else "heuristic")
    if best is not mip and bound is not None and best.objective is not None:
        if abs(bound - best.objective) / max(abs(best.objective), 1e-9) <= mip_gap:
            status = "optimal"
    return Solution(
        status=status,
        objective=best.objective,
        bound=bound,
        open_bundle=best.open_bundle,
        served_kwh=best.served_kwh,
        served_by_cell=best.served_by_cell,
        covered=best.covered,
        cost_inr=best.cost_inr,
        solve_s=elapsed,
        solver=f"{solver_name}+greedy" if best is not mip else solver_name,
    )


# --- why-not ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WhyNot:
    site: int
    objective_delta: float  # forced objective - base objective (<= 0 when base is optimal)
    served_delta_kwh: float
    cost_delta_inr: float
    displaced: list[int]  # base sites that close when this one is forced open
    status: str


def why_not(base: Solution, forced: Solution, site: int) -> WhyNot:
    if forced.objective is None or base.objective is None:
        return WhyNot(site, 0.0, 0.0, 0.0, [], forced.status)
    return WhyNot(
        site=site,
        objective_delta=forced.objective - base.objective,
        served_delta_kwh=forced.total_served_kwh - base.total_served_kwh,
        cost_delta_inr=forced.cost_inr - base.cost_inr,
        displaced=sorted(set(base.open_bundle) - set(forced.open_bundle)),
        status=forced.status,
    )


def deciding_factor(
    site_sub_scores: Mapping[str, float],
    rival_sub_scores: Mapping[str, float],
    weights: Mapping[str, float],
) -> tuple[str, float]:
    """The weighted sub-score where the site trails its rival most (name, points)."""
    gaps = {
        k: weights.get(k, 0.0) * (rival_sub_scores[k] - site_sub_scores[k])
        for k in site_sub_scores
        if weights.get(k, 0.0) > 0
    }
    if not gaps:
        return "none", 0.0
    name = max(gaps, key=lambda k: gaps[k])
    return name, gaps[name]

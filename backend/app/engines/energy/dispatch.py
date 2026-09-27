"""PV + battery sizing by hourly dispatch LP (Section 9.9). Pure: profiles and prices
in, sizes, annual costs and dispatch out. Solved with OR-Tools' GLOP (an LP; the
brief names Pyomo + HiGHS, and the model is solver-agnostic, see the methodology).

Twelve representative days (one per month, weighted by its days), 24 hours each:

    minimise  sum_m days_m sum_h price_h grid_mh           energy bought (INR/yr)
              + demand_charge x 12 x contract_kw / pf       demand charges
              + a_pv kWp + a_e kWh + a_p kW                 annualised PV/battery CAPEX + O&M
    s.t.      pv_mh + grid_mh + dis_mh = load_h + ch_mh     every hour
              pv_mh <= kWp x cf_mh                          (the rest is curtailed; no export)
              soc_m,h+1 = soc_mh + eta ch_mh - dis_mh / eta, with soc_m,24 = soc_m,0
              min_soc kWh <= soc_mh <= max_soc kWh;  ch, dis <= kW
              grid_mh <= contract_kw
              contract_kw + kW >= design_peak_kw            the sub-hourly charging peak
              (max_soc - min_soc) kWh >= kW x peak_hours    ...which the battery must sustain
              kWp <= pv_max;  optional contract_kw <= cap (e.g. the LT limit)

Hourly averages can't see the minutes when every charger draws at once, so the
contract plus battery power must cover the design peak (sanctioned load).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ortools.linear_solver import pywraplp


def annuity_factor(rate: float, years: int) -> float:
    """Capital recovery factor: equal yearly payments that repay 1 over `years`."""
    if rate == 0:
        return 1.0 / years
    return rate * (1 + rate) ** years / ((1 + rate) ** years - 1)


def pv_capacity_factor(
    ghi_wm2: float, temp_c: float, pr: float, temp_coeff: float, cell_rise_c: float
) -> float:
    """AC kW per kWp in an hour with this irradiance and air temperature."""
    if ghi_wm2 <= 0:
        return 0.0
    cell = temp_c + ghi_wm2 / 800.0 * cell_rise_c
    return max(0.0, ghi_wm2 / 1000.0 * pr * (1 + temp_coeff * (cell - 25.0)))


@dataclass(frozen=True)
class EnergyProblem:
    load_kw: Sequence[float]  # 24 hourly means at the meter (after charger losses)
    cf: Sequence[Sequence[float]]  # [12][24] AC kW per kWp
    days: Sequence[int]  # [12]
    price: Sequence[float]  # [24] INR/kWh incl. time-of-day adders
    demand_charge_kva_month: float
    power_factor: float
    design_peak_kw: float
    pv_max_kwp: float
    allow_battery: bool
    allow_pv: bool
    contract_cap_kw: float | None
    annual_cost_per_kwp: float  # annuity + O&M
    annual_cost_per_kwh: float
    annual_cost_per_kw: float
    round_trip_efficiency: float
    min_soc: float
    max_soc: float
    peak_duration_h: float  # how long the sub-hourly peak lasts


@dataclass(frozen=True)
class EnergyResult:
    status: str
    pv_kwp: float
    battery_kwh: float
    battery_kw: float
    contract_kw: float
    annual_energy_cost: float
    annual_demand_cost: float
    annual_pv_cost: float
    annual_battery_cost: float
    grid_kwh: float
    pv_generated_kwh: float
    pv_used_kwh: float
    load_kwh: float
    dispatch: dict[str, list[list[float]]]  # series -> [12][24]

    @property
    def annual_total(self) -> float:
        return (
            self.annual_energy_cost
            + self.annual_demand_cost
            + self.annual_pv_cost
            + self.annual_battery_cost
        )


def solve_dispatch(p: EnergyProblem) -> EnergyResult:
    solver = pywraplp.Solver.CreateSolver("GLOP")
    inf = solver.infinity()
    kwp = solver.NumVar(0.0, p.pv_max_kwp if p.allow_pv else 0.0, "kwp")
    e_cap = solver.NumVar(0.0, inf if p.allow_battery else 0.0, "kwh")
    p_cap = solver.NumVar(0.0, inf if p.allow_battery else 0.0, "kw")
    contract = solver.NumVar(0.0, p.contract_cap_kw if p.contract_cap_kw is not None else inf, "g")
    peak = solver.Constraint(p.design_peak_kw, inf)
    peak.SetCoefficient(contract, 1.0)
    peak.SetCoefficient(p_cap, 1.0)
    # Battery kW that covers the peak needs the energy to deliver it for the peak's duration.
    sustain = solver.Constraint(0.0, inf)
    sustain.SetCoefficient(e_cap, p.max_soc - p.min_soc)
    sustain.SetCoefficient(p_cap, -p.peak_duration_h)

    eta = p.round_trip_efficiency**0.5
    obj = solver.Objective()
    obj.SetCoefficient(contract, p.demand_charge_kva_month * 12 / p.power_factor)
    obj.SetCoefficient(kwp, p.annual_cost_per_kwp)
    obj.SetCoefficient(e_cap, p.annual_cost_per_kwh)
    obj.SetCoefficient(p_cap, p.annual_cost_per_kw)

    series: dict[str, list[list[pywraplp.Variable]]] = {
        k: [] for k in ("pv", "grid", "ch", "dis", "soc")
    }
    for m in range(12):
        row: dict[str, list[pywraplp.Variable]] = {k: [] for k in series}
        for h in range(24):
            pv = solver.NumVar(0.0, inf, "")
            grid = solver.NumVar(0.0, inf, "")
            ch = solver.NumVar(0.0, inf, "")
            dis = solver.NumVar(0.0, inf, "")
            soc = solver.NumVar(0.0, inf, "")
            # Balance: pv + grid + dis - ch = load
            bal = solver.Constraint(p.load_kw[h], p.load_kw[h])
            for v, c in ((pv, 1.0), (grid, 1.0), (dis, 1.0), (ch, -1.0)):
                bal.SetCoefficient(v, c)
            cap_pv = solver.Constraint(-inf, 0.0)  # pv - cf kWp <= 0
            cap_pv.SetCoefficient(pv, 1.0)
            cap_pv.SetCoefficient(kwp, -p.cf[m][h])
            for v in (ch, dis):
                lim = solver.Constraint(-inf, 0.0)
                lim.SetCoefficient(v, 1.0)
                lim.SetCoefficient(p_cap, -1.0)
            gl = solver.Constraint(-inf, 0.0)
            gl.SetCoefficient(grid, 1.0)
            gl.SetCoefficient(contract, -1.0)
            lo = solver.Constraint(0.0, inf)  # soc - min_soc kWh >= 0
            lo.SetCoefficient(soc, 1.0)
            lo.SetCoefficient(e_cap, -p.min_soc)
            hi = solver.Constraint(-inf, 0.0)  # soc - max_soc kWh <= 0
            hi.SetCoefficient(soc, 1.0)
            hi.SetCoefficient(e_cap, -p.max_soc)
            obj.SetCoefficient(grid, p.days[m] * p.price[h])
            for k, v in (("pv", pv), ("grid", grid), ("ch", ch), ("dis", dis), ("soc", soc)):
                row[k].append(v)
        # State of charge carries hour to hour and wraps around the day.
        for h in range(24):
            nxt = row["soc"][(h + 1) % 24]
            link = solver.Constraint(0.0, 0.0)  # nxt - soc - eta ch + dis/eta = 0
            link.SetCoefficient(nxt, 1.0)
            link.SetCoefficient(row["soc"][h], -1.0)
            link.SetCoefficient(row["ch"][h], -eta)
            link.SetCoefficient(row["dis"][h], 1.0 / eta)
        for k in series:
            series[k].append(row[k])
    obj.SetMinimization()

    status = solver.Solve()
    if status != pywraplp.Solver.OPTIMAL:
        return EnergyResult("infeasible", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, {})

    vals = {k: [[v.solution_value() for v in row] for row in rows] for k, rows in series.items()}

    def yearly(grid: list[list[float]], weight: Sequence[float] | None = None) -> float:
        return sum(
            p.days[m] * sum(grid[m][h] * (weight[h] if weight else 1.0) for h in range(24))
            for m in range(12)
        )

    k = kwp.solution_value()
    generated = [[k * p.cf[m][h] for h in range(24)] for m in range(12)]
    return EnergyResult(
        status="optimal",
        pv_kwp=k,
        battery_kwh=e_cap.solution_value(),
        battery_kw=p_cap.solution_value(),
        contract_kw=contract.solution_value(),
        annual_energy_cost=yearly(vals["grid"], p.price),
        annual_demand_cost=p.demand_charge_kva_month
        * 12
        * contract.solution_value()
        / p.power_factor,
        annual_pv_cost=p.annual_cost_per_kwp * k,
        annual_battery_cost=p.annual_cost_per_kwh * e_cap.solution_value()
        + p.annual_cost_per_kw * p_cap.solution_value(),
        grid_kwh=yearly(vals["grid"]),
        pv_generated_kwh=yearly(generated),
        pv_used_kwh=yearly(vals["pv"]),
        load_kwh=sum(p.days) * sum(p.load_kw),
        dispatch={**vals, "pv_available": generated},
    )

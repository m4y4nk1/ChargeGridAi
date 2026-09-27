"""Site finance (Section 9.10). Pure: a sized site and assumptions in, cash flows,
NPV / IRR / payback, Monte Carlo quantiles, a tornado and named cases out.

Cash flows are annual, pre-tax and project-level (no debt), year 0 = CAPEX.

- Energy sold_t = min(capacity cap, served kWh/day x demand multiplier x ramp_t x
  growth^(t - ramp end)) x 365.
- Revenue = energy x selling price.
- OPEX = energy bought (sold / charger efficiency) x grid price + demand charges on
  sanctioned kVA + maintenance and insurance (share of CAPEX) + rent/revenue share,
  roaming and payment fees (share of revenue) + staff.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

import numpy as np
from scipy.optimize import brentq


@dataclass(frozen=True)
class FinanceInputs:
    served_kwh_per_day: float  # steady-state demand the plan assigns to the site
    capacity_kwh_per_day: float  # hard cap: chargers x kW x 24 x max practical utilisation
    capex_lines: Mapping[str, float]  # INR, year 0
    selling_price: float  # INR/kWh
    energy_price: float  # INR/kWh from the grid
    charger_efficiency: float  # kWh delivered per kWh bought
    demand_charge_per_kva_month: float
    sanctioned_kva: float
    maintenance_pct_capex: float
    insurance_pct_capex: float
    rent_share: float
    roaming_share: float
    payment_share: float
    staff_per_year: float
    ramp: Sequence[float]  # share of steady-state demand in years 1..n
    growth: float  # per year after the ramp
    discount_rate: float
    horizon: int
    # Multipliers applied by Monte Carlo / cases / tornado (1.0 = base).
    demand_mult: float = 1.0
    ramp_mult: float = 1.0
    price_mult: float = 1.0
    capex_mult: float = 1.0
    energy_mult: float = 1.0


@dataclass(frozen=True)
class CashFlows:
    years: list[int]
    energy_kwh: list[float]
    revenue: list[float]
    opex: list[float]
    ebitda: list[float]
    net: list[float]  # year 0 = -CAPEX
    capex: float
    npv: float
    irr: float | None
    payback_years: float | None


def _ramp(inputs: FinanceInputs, year: int) -> float:
    ramp = inputs.ramp
    if year <= len(ramp):
        base = ramp[year - 1]
        return float(min(1.0, base * inputs.ramp_mult))
    return float((1 + inputs.growth) ** (year - len(ramp)))


def cash_flows(f: FinanceInputs) -> CashFlows:
    capex = sum(f.capex_lines.values()) * f.capex_mult
    years = list(range(0, f.horizon + 1))
    energy, revenue, opex, ebitda, net = [0.0], [0.0], [0.0], [0.0], [-capex]
    price = f.selling_price * f.price_mult
    grid = f.energy_price * f.energy_mult
    fixed = (
        f.demand_charge_per_kva_month * f.sanctioned_kva * 12
        + (f.maintenance_pct_capex + f.insurance_pct_capex) * capex
        + f.staff_per_year
    )
    variable_share = f.rent_share + f.roaming_share + f.payment_share
    for t in years[1:]:
        kwh_day = min(f.capacity_kwh_per_day, f.served_kwh_per_day * f.demand_mult * _ramp(f, t))
        kwh = kwh_day * 365
        rev = kwh * price
        cost = kwh / f.charger_efficiency * grid + fixed + variable_share * rev
        energy.append(kwh)
        revenue.append(rev)
        opex.append(cost)
        ebitda.append(rev - cost)
        net.append(rev - cost)
    return CashFlows(
        years=years,
        energy_kwh=energy,
        revenue=revenue,
        opex=opex,
        ebitda=ebitda,
        net=net,
        capex=capex,
        npv=npv(net, f.discount_rate),
        irr=irr(net),
        payback_years=payback(net),
    )


def npv(flows: Sequence[float], rate: float) -> float:
    return float(sum(cf / (1 + rate) ** t for t, cf in enumerate(flows)))


def irr(flows: Sequence[float]) -> float | None:
    """Rate where NPV = 0, searched on (-99%, 1000%); None without a sign change."""
    lo, hi = -0.99, 10.0
    f_lo, f_hi = npv(flows, lo), npv(flows, hi)
    if f_lo * f_hi > 0:
        return None
    return float(brentq(lambda r: npv(flows, r), lo, hi, xtol=1e-7))


def payback(flows: Sequence[float]) -> float | None:
    """Years until cumulative undiscounted cash turns positive (interpolated)."""
    cum = flows[0]
    for t in range(1, len(flows)):
        if cum + flows[t] >= 0 and flows[t] > 0:
            return float(t - 1 + (-cum) / flows[t])
        cum += flows[t]
    return None


# --- uncertainty ------------------------------------------------------------------------

MULTIPLIERS = {
    "demand": "demand_mult",
    "utilisation_ramp": "ramp_mult",
    "selling_price": "price_mult",
    "capex": "capex_mult",
    "energy_price": "energy_mult",
}


@dataclass(frozen=True)
class Triangular:
    low: float
    mode: float
    high: float

    def at(self, point: str) -> float:
        return float(getattr(self, point))


def _with(f: FinanceInputs, values: Mapping[str, float]) -> FinanceInputs:
    changes: dict[str, Any] = {MULTIPLIERS[k]: v for k, v in values.items()}
    return replace(f, **changes)


def monte_carlo(
    f: FinanceInputs, dists: Mapping[str, Triangular], draws: int, seed: int
) -> dict[str, dict[str, float | None]]:
    """P10/P50/P90 of NPV, IRR, payback, year-5 EBITDA and monthly revenue at steady state,
    plus the probability NPV > 0."""
    rng = np.random.default_rng(seed)
    samples = {
        k: rng.triangular(d.low, d.mode, d.high, size=draws)
        if d.high > d.low
        else np.full(draws, d.mode)
        for k, d in dists.items()
    }
    npvs, irrs, paybacks, ebitda5, monthly = [], [], [], [], []
    ramp_end = len(f.ramp)
    for i in range(draws):
        cf = cash_flows(_with(f, {k: float(v[i]) for k, v in samples.items()}))
        npvs.append(cf.npv)
        irrs.append(cf.irr)
        paybacks.append(cf.payback_years)
        ebitda5.append(cf.ebitda[min(5, f.horizon)])
        monthly.append(cf.revenue[min(ramp_end, f.horizon)] / 12)

    def q(values: Sequence[float | None], never_is_worst: bool = False) -> dict[str, float | None]:
        # Missing IRR/payback = never pays back: the worst outcome, not a gap to drop.
        worst = np.inf if never_is_worst else -np.inf
        arr = np.array([worst if v is None else v for v in values], dtype=float)
        out: dict[str, float | None] = {}
        for name, p in (("p10", 10), ("p50", 50), ("p90", 90)):
            # 'nearest' never interpolates between two infinities (which gives NaN).
            v = float(np.percentile(arr, p, method="nearest"))
            out[name] = None if not np.isfinite(v) else v
        return out

    npv_arr = np.array(npvs)
    return {
        "npv": q(npvs),
        "irr": q(irrs),
        "payback_years": q(paybacks, never_is_worst=True),
        "ebitda_year5": q(ebitda5),
        "monthly_revenue_steady": q(monthly),
        "prob_npv_positive": {"p": float((npv_arr > 0).mean())},
    }


def tornado(f: FinanceInputs, dists: Mapping[str, Triangular]) -> list[dict[str, Any]]:
    """NPV with each input at its low and high while the others stay at their mode,
    widest swing first."""
    base_values = {k: d.mode for k, d in dists.items()}
    rows: list[dict[str, Any]] = []
    for k, d in dists.items():
        lo = cash_flows(_with(f, {**base_values, k: d.low})).npv
        hi = cash_flows(_with(f, {**base_values, k: d.high})).npv
        rows.append({"input": k, "npv_low_input": lo, "npv_high_input": hi, "swing": abs(hi - lo)})
    return sorted(rows, key=lambda r: -float(r["swing"]))


def cases(
    f: FinanceInputs, dists: Mapping[str, Triangular], definitions: Mapping[str, Mapping[str, str]]
) -> dict[str, CashFlows]:
    return {
        name: cash_flows(_with(f, {k: dists[k].at(point) for k, point in points.items()}))
        for name, points in definitions.items()
    }

"""Existing supply vs demand, per H3 cell (Section 9.3).

Uses the two-step floating catchment area (2SFCA) method so a station's
capacity is shared across the cells it serves rather than counted once per
cell:

1. Each station's supply-to-demand ratio R_j = S_j / sum of demand in its
   drive-time catchment.
2. Each cell's access ratio A_i = sum of R_j over stations whose catchment
   covers it.

Demand served in cell i is D_i * min(1, A_i); the gap is D_i * max(0, 1 - A_i).
A_i above 1 means the cell is (in aggregate) over-served.

Station daily supply S_j = sum over charge points of kW x availability x
target utilisation x 24 h. Charge points with no power data use an assumed
kW; stations listing no charge points count as an assumed number. Both are
reported so the reader knows how much of "supply" is assumption.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray


@dataclass(frozen=True)
class SupplyAssumptions:
    availability: float
    target_utilisation: float
    assumed_kw_when_unknown: float
    assumed_charge_points_when_unknown: int
    new_charge_point_kw: float


@dataclass(frozen=True)
class StationSupply:
    station_id: str
    kwh_per_day: float
    charge_points: int
    assumed_kw_charge_points: int  # charge points whose kW was assumed
    assumed_count: bool  # station listed no charge points; count was assumed


def charge_point_kwh_per_day(kw: float, a: SupplyAssumptions) -> float:
    return kw * a.availability * a.target_utilisation * 24.0


def station_supply(
    station_id: str, evses: Sequence[tuple[float | None, int]], a: SupplyAssumptions
) -> StationSupply:
    """evses: (max_kw or None, count) per EVSE group."""
    if not evses:
        n = a.assumed_charge_points_when_unknown
        return StationSupply(
            station_id, n * charge_point_kwh_per_day(a.assumed_kw_when_unknown, a), n, n, True
        )
    kwh = 0.0
    assumed = 0
    for kw, count in evses:
        if kw is None:
            assumed += count
        kwh += count * charge_point_kwh_per_day(
            kw if kw is not None else a.assumed_kw_when_unknown, a
        )
    return StationSupply(station_id, kwh, sum(c for _, c in evses), assumed, False)


@dataclass(frozen=True)
class GapResult:
    access_ratio: NDArray[np.float64]
    served_kwh: NDArray[np.float64]
    gap_kwh: NDArray[np.float64]
    unused_supply_kwh: float  # supply at stations whose catchment has no demand


def two_step_fca(
    demand_kwh: NDArray[np.float64],
    station_kwh: Sequence[float],
    catchments: Sequence[Sequence[int]],
) -> GapResult:
    """catchments[j] lists the cell indices station j's drive-time catchment covers."""
    demand = np.asarray(demand_kwh, dtype=float)
    if (demand < 0).any():
        raise ValueError("demand must be non-negative")
    access = np.zeros_like(demand)
    unused = 0.0
    for supply, cells in zip(station_kwh, catchments, strict=True):
        idx = np.asarray(cells, dtype=int)
        catchment_demand = float(demand[idx].sum()) if len(idx) else 0.0
        if catchment_demand <= 0:
            unused += supply
            continue
        access[idx] += supply / catchment_demand
    served = demand * np.minimum(1.0, access)
    return GapResult(access, served, demand - served, unused)


def additional_charge_points(gap_kwh_per_day: float, a: SupplyAssumptions) -> int:
    per_point = charge_point_kwh_per_day(a.new_charge_point_kw, a)
    return math.ceil(gap_kwh_per_day / per_point) if gap_kwh_per_day > 0 else 0

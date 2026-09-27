"""Digital-twin scenarios over whole regions (Phase 12): fast what-ifs on the gap layer,
without candidate generation or catchments.

`allocate` places N chargers of one class on the cells with the largest unserved
demand, one charger at a time: each charger delivers `kwh_per_charger` a day (same
availability x utilisation x 24 h assumption as the gap model) and goes to the cell
with the most gap left, up to `max_per_cell`. It is a screening estimate of where
chargers would do the most good, not a site plan.
"""

from __future__ import annotations

import heapq
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class Allocation:
    chargers: dict[str, int]  # cell -> chargers placed
    served_kwh: dict[str, float]  # cell -> newly served kWh/day
    placed: int
    unplaced: int  # chargers left over once every gap was covered (demand-limited)
    gap_before: float
    gap_after: float


def allocate(
    gap: Mapping[str, float], n: int, kwh_per_charger: float, max_per_cell: int = 20
) -> Allocation:
    if n < 0 or kwh_per_charger <= 0 or max_per_cell < 1:
        raise ValueError("n >= 0, kwh_per_charger > 0 and max_per_cell >= 1 required")
    remaining = {c: float(v) for c, v in gap.items() if v and v > 0}
    heap = [(-v, c) for c, v in remaining.items()]
    heapq.heapify(heap)
    chargers: dict[str, int] = {}
    served: dict[str, float] = {}
    placed = 0
    while placed < n and heap:
        neg, cell = heapq.heappop(heap)
        left = -neg
        take = min(kwh_per_charger, left)
        chargers[cell] = chargers.get(cell, 0) + 1
        served[cell] = served.get(cell, 0.0) + take
        placed += 1
        left -= take
        remaining[cell] = left
        if left > 1e-9 and chargers[cell] < max_per_cell:
            heapq.heappush(heap, (-left, cell))
    before = float(sum(v for v in gap.values() if v and v > 0))
    return Allocation(chargers, served, placed, n - placed, before, before - sum(served.values()))

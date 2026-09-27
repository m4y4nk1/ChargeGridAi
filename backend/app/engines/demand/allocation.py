"""Dasymetric allocation of RTO-level EV stock to H3 cells (Section 9.2 step 1).

A cell's share for a segment is the weighted average of its share of each
feature within the catchment:
    share_i = sum_f w_f * x_{f,i} / sum_j x_{f,j}   (over features with any mass)
Shares sum to exactly 1, so allocating a total multiplies it by the shares
and the RTO total is preserved by construction. A feature that is zero
everywhere in the catchment drops out and its weight is spread over the rest;
if every feature is empty the stock is spread uniformly.
"""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np
from numpy.typing import NDArray


def allocation_shares(
    features: Mapping[str, NDArray[np.float64]], weights: Mapping[str, float], n_cells: int
) -> NDArray[np.float64]:
    if n_cells == 0:
        return np.zeros(0)
    if any(w < 0 for w in weights.values()):
        raise ValueError("allocation weights must be non-negative")

    combined = np.zeros(n_cells)
    used_weight = 0.0
    for name, weight in weights.items():
        if weight == 0:
            continue
        x = np.asarray(features[name], dtype=float)
        if x.shape != (n_cells,):
            raise ValueError(f"feature {name} has shape {x.shape}, expected ({n_cells},)")
        if (x < 0).any():
            raise ValueError(f"feature {name} has negative values")
        total = x.sum()
        if total <= 0:
            continue
        combined += weight * x / total
        used_weight += weight

    if used_weight == 0:
        return np.full(n_cells, 1.0 / n_cells)
    return combined / used_weight

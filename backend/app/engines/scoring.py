"""Scoring engine (Section 9.6). Pure: raw measurements in, sub-scores, totals and rank
robustness out.

Sub-scores are 0-100 percentile ranks within the scored set (the run's feasible
candidates), ties sharing their average rank, so a score says "better than X% of
the alternatives" rather than an absolute quality. `total = sum(w_k * s_k)` with
weights from a profile that sums to 1.

Rank robustness perturbs the weights with a Dirichlet centred on the profile
(concentration c gives each weight a coefficient of variation of
sqrt((1 - w) / (w * (c + 1))), about 20% at w = 0.2 when c = 100) and reports the
share of draws in which each site stays in the top N.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import numpy as np
from numpy.typing import NDArray

SUB_SCORES = (
    "demand",
    "accessibility",
    "traffic",
    "grid",
    "land",
    "future_growth",
    "competition_gap",
    "equity",
)


def percentile_rank(values: Sequence[float], higher_is_better: bool = True) -> NDArray[np.float64]:
    """0-100 average-rank percentile. The best value scores 100, the worst 0; ties share."""
    x = np.asarray(values, dtype=float)
    n = len(x)
    if n == 0:
        return np.zeros(0)
    if n == 1:
        return np.full(1, 100.0)
    if not higher_is_better:
        x = -x
    order = np.argsort(x, kind="mergesort")
    ranks = np.empty(n)
    ranks[order] = np.arange(n, dtype=float)
    # Average the ranks of tied values.
    _, inverse, counts = np.unique(x, return_inverse=True, return_counts=True)
    sums = np.zeros(len(counts))
    np.add.at(sums, inverse, ranks)
    return 100.0 * (sums / counts)[inverse] / (n - 1)


def validate_weights(weights: Mapping[str, float]) -> dict[str, float]:
    unknown = sorted(set(weights) - set(SUB_SCORES))
    if unknown:
        raise ValueError(f"unknown sub-scores {unknown}")
    if any(w < 0 for w in weights.values()):
        raise ValueError("weights must be non-negative")
    total = sum(weights.values())
    if total <= 0:
        raise ValueError("at least one weight must be positive")
    # Profiles are specified to sum to 1; custom weights are normalised.
    return {k: float(weights.get(k, 0.0)) / total for k in SUB_SCORES}


def score_matrix(sub_scores: Sequence[Mapping[str, float]]) -> NDArray[np.float64]:
    return np.array([[s[k] for k in SUB_SCORES] for s in sub_scores], dtype=float).reshape(
        len(sub_scores), len(SUB_SCORES)
    )


def totals(matrix: NDArray[np.float64], weights: Mapping[str, float]) -> NDArray[np.float64]:
    w = np.array([weights[k] for k in SUB_SCORES])
    result: NDArray[np.float64] = matrix @ w
    return result


def rank_order(total: NDArray[np.float64]) -> NDArray[np.int64]:
    """Indices best-first; ties keep input order so reruns rank identically."""
    return np.argsort(-total, kind="mergesort")


def rank_robustness(
    matrix: NDArray[np.float64],
    weights: Mapping[str, float],
    top_n: int,
    draws: int,
    concentration: float,
    seed: int,
) -> NDArray[np.float64]:
    """Share of Dirichlet weight draws in which each site ranks within the top N.

    Zero-weight sub-scores stay at zero: the profile chose to ignore them, and
    perturbing a weight means varying how much, not whether, it counts.
    """
    n = matrix.shape[0]
    if n == 0:
        return np.zeros(0)
    top_n = min(top_n, n)
    w = np.array([weights[k] for k in SUB_SCORES])
    active = w > 0
    rng = np.random.default_rng(seed)
    sampled = rng.dirichlet(concentration * w[active], size=draws)  # draws x k
    scores = matrix[:, active] @ sampled.T  # n x draws
    if top_n == n:
        return np.ones(n)
    # Per draw, the top-N set; ties at the cut resolve by index, as in rank_order.
    top = np.argpartition(-scores, top_n - 1, axis=0)[:top_n]
    hits = np.zeros(n)
    np.add.at(hits, top.ravel(), 1)
    result: NDArray[np.float64] = hits / draws
    return result


def cagr(start: float, end: float, years: int) -> float | None:
    if years <= 0 or start <= 0 or end < 0:
        return None
    return float((end / start) ** (1 / years) - 1)


def index_vs_benchmark(value: float | None, benchmark: float | None) -> float | None:
    """Catchment index where the regional benchmark = 100."""
    if value is None or benchmark is None or benchmark <= 0:
        return None
    return 100.0 * value / benchmark

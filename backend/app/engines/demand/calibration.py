"""Calibration of station demand against observed utilisation (Section 9.2 step 5).

Parametric prediction for an existing station j, consistent with the 2SFCA gap engine:
cell i's served energy is shared among the stations covering it in proportion to their
supply-to-demand ratios, so

    R_j = S_j / sum_{i in C_j} D_i
    parametric_j = sum_{i in C_j} served_i * R_j / A_i      (A_i = sum_k R_k > 0)

A LightGBM model is trained on the residual (observed - parametric) and the calibrated
prediction is parametric + residual, floored at zero. Metrics are out-of-fold (K-fold),
reported for both models; the parametric model stays the reference.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray


def station_parametric_kwh(
    supply_kwh: float,
    catchment: Sequence[str],
    demand: Mapping[str, float],
    served: Mapping[str, float],
    access: Mapping[str, float],
) -> float:
    total_demand = sum(demand.get(c, 0.0) or 0.0 for c in catchment)
    if total_demand <= 0 or supply_kwh <= 0:
        return 0.0
    r = supply_kwh / total_demand
    return float(
        sum((served.get(c) or 0.0) * r / a for c in catchment if (a := access.get(c) or 0.0) > 0)
    )


def metrics(observed: NDArray[np.float64], predicted: NDArray[np.float64]) -> dict[str, float]:
    err = predicted - observed
    ss_res = float((err**2).sum())
    ss_tot = float(((observed - observed.mean()) ** 2).sum())
    return {
        "mae": float(np.abs(err).mean()),
        "rmse": math.sqrt(float((err**2).mean())),
        "bias": float(err.mean()),
        "r2": 1 - ss_res / ss_tot if ss_tot > 0 else float("nan"),
    }


@dataclass
class CalibrationResult:
    parametric: dict[str, float]
    calibrated: dict[str, float]
    oof_calibrated: NDArray[np.float64]
    booster: Any
    feature_importance: dict[str, float]


def _frame(features: Sequence[Mapping[str, Any]], columns: Sequence[str]) -> Any:
    import pandas as pd

    df = pd.DataFrame([{c: f.get(c) for c in columns} for f in features])
    for c in columns:
        if not pd.api.types.is_numeric_dtype(df[c]):
            df[c] = df[c].astype("category")
    return df


def calibrate(
    features: Sequence[Mapping[str, Any]],
    columns: Sequence[str],
    observed: Sequence[float],
    parametric: Sequence[float],
    params: Mapping[str, Any],
    folds: int,
    seed: int,
) -> CalibrationResult:
    import lightgbm as lgb

    y = np.asarray(observed, dtype=float)
    base = np.asarray(parametric, dtype=float)
    if len(y) < folds * 2:
        raise ValueError(f"need at least {folds * 2} stations for {folds}-fold validation")
    X = _frame(features, columns)
    residual = y - base
    lgb_params = {k: v for k, v in params.items() if k != "num_boost_round"}
    lgb_params["seed"] = seed
    rounds = int(params.get("num_boost_round", 200))
    order = np.random.default_rng(seed).permutation(len(y))
    oof = np.zeros(len(y))
    for k in range(folds):
        test = order[k::folds]
        train = np.setdiff1d(order, test)
        booster = lgb.train(lgb_params, lgb.Dataset(X.iloc[train], residual[train]), rounds)
        oof[test] = booster.predict(X.iloc[test])
    calibrated = np.maximum(0.0, base + oof)
    final = lgb.train(lgb_params, lgb.Dataset(X, residual), rounds)
    gain = final.feature_importance(importance_type="gain")
    return CalibrationResult(
        parametric=metrics(y, base),
        calibrated=metrics(y, calibrated),
        oof_calibrated=calibrated,
        booster=final,
        feature_importance={c: float(g) for c, g in zip(columns, gain, strict=True)},
    )

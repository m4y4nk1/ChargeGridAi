"""Demand calibration: parametric station prediction, LightGBM residual model, MLflow."""

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from app.engines.demand.calibration import calibrate, metrics, station_parametric_kwh

COLUMNS = ["parametric_kwh_per_day", "charge_points", "host_type"]
PARAMS = {
    "objective": "regression",
    "learning_rate": 0.1,
    "num_leaves": 7,
    "min_data_in_leaf": 3,
    "num_boost_round": 150,
    "verbose": -1,
}


def test_station_share_of_served_energy() -> None:
    # Two cells; station supply 50 over catchment demand 200 -> R = 0.25.
    demand = {"a": 100.0, "b": 100.0}
    served = {"a": 100.0, "b": 50.0}
    access = {"a": 1.0, "b": 0.5}
    # a: 100 * 0.25 / 1.0 = 25; b: 50 * 0.25 / 0.5 = 25
    assert station_parametric_kwh(50.0, ["a", "b"], demand, served, access) == 50.0
    assert station_parametric_kwh(50.0, [], demand, served, access) == 0.0


def _synthetic(n: int = 80) -> tuple[list[dict[str, Any]], list[float], list[float]]:
    """Test-only data: observed use is 1.6x the parametric at fuel stations, 0.7x at malls."""
    rng = np.random.default_rng(1)
    feats, obs, par = [], [], []
    for i in range(n):
        p = float(rng.uniform(50, 500))
        host = "FUEL_STATION" if i % 2 else "MALL"
        feats.append(
            {
                "parametric_kwh_per_day": p,
                "charge_points": int(rng.integers(1, 6)),
                "host_type": host,
            }
        )
        obs.append(p * (1.6 if host == "FUEL_STATION" else 0.7) + float(rng.normal(0, 10)))
        par.append(p)
    return feats, obs, par


def test_residual_model_beats_parametric_out_of_fold() -> None:
    feats, obs, par = _synthetic()
    result = calibrate(feats, COLUMNS, obs, par, PARAMS, folds=5, seed=42)
    assert result.calibrated["mae"] < 0.5 * result.parametric["mae"]
    assert result.calibrated["r2"] > result.parametric["r2"]
    assert (result.oof_calibrated >= 0).all()
    assert result.feature_importance["host_type"] > 0


def test_too_few_stations() -> None:
    feats, obs, par = _synthetic(6)
    with pytest.raises(ValueError, match="at least"):
        calibrate(feats, COLUMNS, obs, par, PARAMS, folds=5, seed=1)


def test_metrics() -> None:
    m = metrics(np.array([1.0, 2.0, 3.0]), np.array([1.0, 2.0, 4.0]))
    assert m["mae"] == pytest.approx(1 / 3) and m["bias"] == pytest.approx(1 / 3)


def test_mlflow_logging_both_models(tmp_path: Path, monkeypatch: Any) -> None:
    import mlflow

    from app.services import calibration as svc

    uri = f"file://{tmp_path}/mlruns"
    monkeypatch.setattr(svc, "_mlflow", lambda: (mlflow.set_tracking_uri(uri), mlflow)[1])
    feats, obs, par = _synthetic(40)
    result = calibrate(feats, COLUMNS, obs, par, PARAMS, folds=4, seed=3)
    rows = [
        {
            "station_id": f"s{i}",
            "observed_kwh_per_day": o,
            "features": {"parametric_kwh_per_day": p},
        }
        for i, (o, p) in enumerate(zip(obs, par, strict=True))
    ]
    cfg = {
        "experiment": "test-cal",
        "registered_model": "test_residual",
        "min_stations": 30,
        "min_days_observed": 28,
        "window_days": 90,
        "catchment_minutes": 10,
        "folds": 4,
        "features": COLUMNS,
        "lightgbm": PARAMS,
    }
    summary = {"status": "calibrated", "stations_observed": 40, "stations_used": 40}
    run_id = svc._log_to_mlflow(cfg, summary, rows, result)
    run = mlflow.get_run(run_id)
    assert run.data.tags["replaces_parametric"] == "false"
    assert run.data.metrics["calibrated_mae"] < run.data.metrics["parametric_mae"]
    artifacts = {a.path for a in mlflow.MlflowClient().list_artifacts(run_id)}
    assert {"station_predictions.csv", "feature_importance.json", "model"} <= artifacts
    skipped = svc._log_to_mlflow(
        cfg,
        {"status": "skipped_insufficient_data", "stations_observed": 3, "stations_used": 0},
        [],
        None,
    )
    assert mlflow.get_run(skipped).data.tags["status"] == "skipped_insufficient_data"

"""Demand calibration run (Section 9.2 step 5): observed station utilisation from
operator feeds vs the parametric model, a LightGBM residual model on top, both logged
to MLflow. The parametric model is never replaced; calibrated predictions are stored
beside it (station_prediction) and labelled.

Too few observed stations (config/calibration.yaml `min_stations`) -> the run is
logged to MLflow as skipped, with the counts, and nothing is trained.
"""

from __future__ import annotations

import asyncio
import csv
import io
import tempfile
import uuid
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.assumptions import load_config
from app.core.regions import all_regions, default_region_id, scenario_key
from app.core.settings import get_settings
from app.db.models.charging import ChargingStation, Evse
from app.db.models.operations import StationPrediction, StationUtilisationDaily
from app.db.repositories.demand import supply_assumptions
from app.engines.demand.calibration import calibrate as fit_calibration
from app.engines.demand.calibration import station_parametric_kwh
from app.engines.supply_gap import station_supply
from app.providers.base import Mode, Point
from app.providers.valhalla.routing import ValhallaRoutingProvider

PARAMETRIC_MODEL = "demand_parametric_v1 (Bass + dasymetric + 2SFCA)"


def _mlflow() -> Any:
    import mlflow

    mlflow.set_tracking_uri(get_settings().mlflow_tracking_uri)
    return mlflow


async def observed_stations(
    session: AsyncSession, window_days: int, min_days: int
) -> tuple[dict[uuid.UUID, dict[str, Any]], date | None]:
    latest = await session.scalar(select(func.max(StationUtilisationDaily.day)))
    if latest is None:
        return {}, None
    start = latest - timedelta(days=window_days - 1)
    rows = (
        await session.execute(
            select(
                StationUtilisationDaily.station_id,
                func.min(StationUtilisationDaily.day),
                func.max(StationUtilisationDaily.day),
                func.sum(StationUtilisationDaily.kwh),
                func.sum(StationUtilisationDaily.sessions),
                func.max(StationUtilisationDaily.source_id),
            )
            .where(StationUtilisationDaily.day >= start)
            .group_by(StationUtilisationDaily.station_id)
        )
    ).all()
    out = {}
    for sid, first, last, kwh, sessions, source in rows:
        # Days without sessions inside the observed span count as zero-energy days.
        days = (last - first).days + 1
        if days >= min_days:
            out[sid] = {
                "days": days,
                "kwh_per_day": float(kwh) / days,
                "sessions_per_day": float(sessions) / days,
                "source": source,
            }
    return out, latest


async def _cell_values(
    session: AsyncSession, region_id: str, year: int
) -> dict[str, dict[str, float]]:
    sc = scenario_key(region_id, "history")
    rows = (
        await session.execute(
            text(
                "select f.h3::text, f.public_kwh_p50, f.served_kwh, f.access_ratio, "
                "f.population, f.ev_stock_p50 from h3_cell_feature f "
                "join region_cell rc on rc.h3 = f.h3 and rc.region_id = :r "
                "where f.scenario = :sc and f.year = :y"
            ),
            {"r": region_id, "sc": sc, "y": year},
        )
    ).all()
    return {
        c: {
            "demand": d or 0.0,
            "served": s or 0.0,
            "access": a or 0.0,
            "population": p or 0.0,
            "evs": e or 0.0,
        }
        for c, d, s, a, p, e in rows
    }


async def build_dataset(
    session: AsyncSession, observed: dict[uuid.UUID, dict[str, Any]], year: int, minutes: int
) -> list[dict[str, Any]]:
    from app.services.scoring_run import _cells_in, _isochrone_shape

    assumptions, _ = supply_assumptions()
    stations = (
        await session.execute(
            select(
                ChargingStation.id,
                ChargingStation.host_type,
                func.ST_Y(ChargingStation.geom),
                func.ST_X(ChargingStation.geom),
            ).where(ChargingStation.id.in_(list(observed)))
        )
    ).all()
    evses: dict[uuid.UUID, list[tuple[float | None, int, str | None]]] = defaultdict(list)
    for sid, kw, count, current in (
        await session.execute(
            select(Evse.station_id, Evse.max_kw, Evse.count, Evse.current).where(
                Evse.station_id.in_(list(observed))
            )
        )
    ).all():
        evses[sid].append((kw, count, current))
    cells_by_region: dict[str, dict[str, dict[str, float]]] = {}
    router = ValhallaRoutingProvider()
    rows = []
    for sid, host, lat, lng in stations:
        region = next(
            (
                r.id
                for r in sorted(all_regions(), key=lambda r: r.id != default_region_id())
                if r.bbox.contains(lat, lng)
            ),
            None,
        )
        if region is None:
            continue
        if region not in cells_by_region:
            cells_by_region[region] = await _cell_values(session, region, year)
        values = cells_by_region[region]
        shape = _isochrone_shape(
            await router.isochrone(Point(lat=lat, lng=lng), [minutes], Mode.DRIVE)
        )
        catchment = [c for c in (_cells_in(shape, lat, lng) if shape else []) if c in values]
        ev = evses.get(sid, [])
        supply = station_supply(str(sid), [(kw, n) for kw, n, _ in ev], assumptions)
        points = sum(n for _, n, _ in ev) or supply.charge_points
        kws = [kw for kw, _, _ in ev if kw]
        parametric = station_parametric_kwh(
            supply.kwh_per_day,
            catchment,
            {c: values[c]["demand"] for c in catchment},
            {c: values[c]["served"] for c in catchment},
            {c: values[c]["access"] for c in catchment},
        )
        access = [values[c]["access"] for c in catchment]
        rows.append(
            {
                "station_id": sid,
                "observed_kwh_per_day": observed[sid]["kwh_per_day"],
                "features": {
                    "parametric_kwh_per_day": parametric,
                    "charge_points": points,
                    "max_kw": max(kws) if kws else None,
                    "dc_share": (sum(n for _, n, c in ev if c == "DC") / points) if points else 0.0,
                    "catchment_population": sum(values[c]["population"] for c in catchment),
                    "catchment_evs": sum(values[c]["evs"] for c in catchment),
                    "mean_access_ratio": sum(access) / len(access) if access else 0.0,
                    "host_type": host or "unknown",
                },
            }
        )
    return rows


def _log_to_mlflow(
    cfg: dict[str, Any], summary: dict[str, Any], rows: list[dict[str, Any]], result: Any
) -> str:
    mlflow = _mlflow()
    mlflow.set_experiment(cfg["experiment"])
    with mlflow.start_run(run_name=f"calibration-{datetime.now(UTC):%Y%m%dT%H%M}") as run:
        mlflow.set_tags(
            {
                "status": summary["status"],
                "parametric_model": PARAMETRIC_MODEL,
                "replaces_parametric": "false",
            }
        )
        mlflow.log_params(
            {
                "min_stations": cfg["min_stations"],
                "min_days_observed": cfg["min_days_observed"],
                "window_days": cfg["window_days"],
                "catchment_minutes": cfg["catchment_minutes"],
                "folds": cfg["folds"],
                "features": ",".join(cfg["features"]),
                **{f"lgb_{k}": v for k, v in cfg["lightgbm"].items()},
            }
        )
        mlflow.log_metrics(
            {
                "n_stations_observed": summary["stations_observed"],
                "n_stations_used": summary["stations_used"],
            }
        )
        if result is not None:
            mlflow.log_metrics({f"parametric_{k}": v for k, v in result.parametric.items()})
            mlflow.log_metrics({f"calibrated_{k}": v for k, v in result.calibrated.items()})
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow(["station_id", "observed", "parametric", "calibrated_oof"])
            for r, cal in zip(rows, result.oof_calibrated, strict=True):
                writer.writerow(
                    [
                        r["station_id"],
                        r["observed_kwh_per_day"],
                        r["features"]["parametric_kwh_per_day"],
                        float(cal),
                    ]
                )
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "station_predictions.csv"
                path.write_text(buf.getvalue())
                mlflow.log_artifact(str(path))
            mlflow.log_dict(result.feature_importance, "feature_importance.json")
            import mlflow.lightgbm

            mlflow.lightgbm.log_model(
                result.booster, "model", registered_model_name=cfg["registered_model"]
            )
        return str(run.info.run_id)


async def calibrate(session: AsyncSession) -> dict[str, Any]:
    cfg = load_config("calibration.yaml")
    observed, latest = await observed_stations(
        session, int(cfg["window_days"]), int(cfg["min_days_observed"])
    )
    summary: dict[str, Any] = {
        "stations_observed": len(observed),
        "stations_used": 0,
        "window_end": str(latest) if latest else None,
        "min_stations": cfg["min_stations"],
    }
    rows: list[dict[str, Any]] = []
    result = None
    if len(observed) < int(cfg["min_stations"]):
        summary["status"] = "skipped_insufficient_data"
        summary["note"] = (
            f"{len(observed)} stations have {cfg['min_days_observed']}+ days of observed "
            f"sessions; calibration needs {cfg['min_stations']}. Connect operator feeds "
            "(config/ocpi_operators.yaml) first. The parametric model remains in use."
        )
    else:
        assert latest is not None
        rows = await build_dataset(session, observed, latest.year, int(cfg["catchment_minutes"]))
        summary["stations_used"] = len(rows)
        result = await asyncio.to_thread(
            fit_calibration,
            [r["features"] for r in rows],
            cfg["features"],
            [r["observed_kwh_per_day"] for r in rows],
            [r["features"]["parametric_kwh_per_day"] for r in rows],
            cfg["lightgbm"],
            int(cfg["folds"]),
            int(cfg["seed"]),
        )
        summary["status"] = "calibrated"
        summary["parametric"] = result.parametric
        summary["calibrated"] = result.calibrated
    try:
        run_id = await asyncio.to_thread(_log_to_mlflow, cfg, summary, rows, result)
        summary["mlflow_run_id"] = run_id
    except Exception as exc:  # tracking server down: report, don't lose the result
        summary["mlflow_error"] = f"{type(exc).__name__}: {exc}"
        run_id = f"local-{uuid.uuid4()}"
    if result is not None:
        session.add_all(
            StationPrediction(
                calibration_run=run_id,
                station_id=r["station_id"],
                observed_kwh_per_day=r["observed_kwh_per_day"],
                parametric_kwh_per_day=r["features"]["parametric_kwh_per_day"],
                calibrated_kwh_per_day=float(cal),
                features={k: v for k, v in r["features"].items()},
            )
            for r, cal in zip(rows, result.oof_calibrated, strict=True)
        )
        await session.commit()
    return summary

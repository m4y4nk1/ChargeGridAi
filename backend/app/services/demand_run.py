"""Demand and gap run (Sections 9.2-9.3): registrations -> cells -> kWh -> gap.

Orchestrates the pure engines against the database and Valhalla:

1. EV registrations (VAHAN, or the synthetic stand-in) per RTO x segment.
2. Bass fit + bootstrap once per series; slow/base/fast (x what-if multiplier)
   rescale only post-observation registrations.
3. Stock allocated to H3 cells within each RTO's catchment by the feature
   weights in config/demand/allocation.yaml (totals preserved exactly).
4. Public kWh/day and sessions per cell from config/demand/segments.yaml.
5. Existing supply from fused stations over Valhalla drive-time catchments,
   2SFCA gap at P50 demand.
6. `history` + forecast rows in h3_cell_feature, and evidence rows carrying
   the full configuration, placeholders in use, and input snapshots.

Anything built on synthetic registrations is confidence NONE.
"""

from __future__ import annotations

import json
import os
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Any

import h3
import numpy as np
from geoalchemy2.shape import from_shape
from numpy.typing import NDArray
from shapely.geometry import Point as ShapelyPoint
from shapely.geometry import Polygon as ShapelyPolygon
from shapely.prepared import prep
from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.assumptions import Assumptions, load_config
from app.core.regions import default_region_id, get_region, scenario_key
from app.db.models.charging import ChargerSourceRecord, ChargingStation, Evse
from app.db.models.demand_inputs import H3Cell, H3CellFeature, RegionCell, VehicleRegistrationAgg
from app.db.models.provenance import DatasetSnapshot, Evidence
from app.engines.demand.adoption_bass import forecast_paths, stock_quantiles
from app.engines.demand.allocation import allocation_shares
from app.engines.demand.charging_demand import SegmentUsage, public_kwh_per_day, sessions_per_day
from app.engines.supply_gap import (
    StationSupply,
    SupplyAssumptions,
    additional_charge_points,
    station_supply,
    two_step_fca,
)
from app.providers.base import Mode, Point
from app.providers.valhalla.routing import ValhallaRoutingProvider

PLUG_IN_EV_TYPES = ("BEV", "PHEV")  # hybrids and fuel cells never plug in
CASES = ("slow", "base", "fast")
METHOD = "demand_v1"


class DemandRunError(RuntimeError):
    pass


@dataclass
class DemandRunSummary:
    run_id: str
    scenarios: list[str]
    years: list[int]
    cells: int
    registration_source: str
    observed_through: str
    placeholders_in_use: list[str]
    region_totals: dict[str, dict[int, dict[str, float]]]


def scenario_name(case: str, multiplier: float, region_id: str | None = None) -> str:
    """h3_cell_feature scenario key: "base", "fastx1.3"; namespaced outside the default
    region ("mumbai_pune:base") because regions overlap."""
    name = case if multiplier == 1.0 else f"{case}x{multiplier:g}"
    return scenario_key(region_id or default_region_id(), name)


def _month_index(d: date, start: date) -> int:
    return (d.year - start.year) * 12 + (d.month - start.month)


async def _load_registrations(
    session: AsyncSession,
) -> tuple[dict[tuple[str, str], NDArray[np.float64]], date, date, set[str], list[uuid.UUID]]:
    rows = (
        await session.execute(
            select(
                VehicleRegistrationAgg.rto_code,
                VehicleRegistrationAgg.segment,
                VehicleRegistrationAgg.month,
                func.sum(VehicleRegistrationAgg.count),
                DatasetSnapshot.source_id,
                DatasetSnapshot.id,
            )
            .join(DatasetSnapshot, DatasetSnapshot.id == VehicleRegistrationAgg.snapshot_id)
            .where(VehicleRegistrationAgg.ev_type.in_(PLUG_IN_EV_TYPES))
            .group_by(
                VehicleRegistrationAgg.rto_code,
                VehicleRegistrationAgg.segment,
                VehicleRegistrationAgg.month,
                DatasetSnapshot.source_id,
                DatasetSnapshot.id,
            )
        )
    ).all()
    if not rows:
        raise DemandRunError("No plug-in EV registrations loaded; run a VAHAN ingestion first")

    start = min(r[2] for r in rows)
    end = max(r[2] for r in rows)
    n = _month_index(end, start) + 1
    series: dict[tuple[str, str], NDArray[np.float64]] = defaultdict(lambda: np.zeros(n))
    for rto, segment, month, count, _, _ in rows:
        series[(rto, segment)][_month_index(month, start)] += float(count)
    sources = {r[4] for r in rows}
    snapshots = sorted({r[5] for r in rows})
    return dict(series), start, end, sources, snapshots


async def _region_cells(
    session: AsyncSession, region_id: str, res: int
) -> tuple[list[str], NDArray[np.float64], NDArray[np.float64]]:
    """Every res-`res` cell in the region box, created if missing; returns ids + centroids."""
    box = get_region(region_id).bbox
    ring = [
        (box.min_lat, box.min_lng),
        (box.min_lat, box.max_lng),
        (box.max_lat, box.max_lng),
        (box.max_lat, box.min_lng),
    ]
    cells = sorted(h3.polygon_to_cells(h3.LatLngPoly(ring), res))
    existing = set(
        (
            await session.execute(select(RegionCell.h3).where(RegionCell.region_id == region_id))
        ).scalars()
    )
    for cell in cells:
        if cell in existing:
            continue
        boundary = [(lng, lat) for lat, lng in h3.cell_to_boundary(cell)]
        await session.execute(
            insert(H3Cell)
            .values(
                h3=cell,
                res=res,
                region_id=region_id,
                geom=from_shape(ShapelyPolygon(boundary), srid=4326),
            )
            .on_conflict_do_nothing(index_elements=[H3Cell.h3])
        )
        await session.execute(
            insert(RegionCell).values(region_id=region_id, h3=cell).on_conflict_do_nothing()
        )
    await session.flush()
    all_cells = sorted(existing | set(cells))
    centroids = np.array([h3.cell_to_latlng(c) for c in all_cells])
    return all_cells, centroids[:, 0], centroids[:, 1]


async def _features(
    session: AsyncSession, cells: list[str], res: int, feature_cfg: dict[str, Any]
) -> tuple[dict[str, NDArray[np.float64]], list[uuid.UUID]]:
    index = {c: i for i, c in enumerate(cells)}
    features: dict[str, NDArray[np.float64]] = {name: np.zeros(len(cells)) for name in feature_cfg}
    snapshots: set[uuid.UUID] = set()

    if "population" in feature_cfg:
        latest_year = await session.scalar(
            select(func.max(H3CellFeature.year)).where(
                H3CellFeature.scenario == "observed", H3CellFeature.population.is_not(None)
            )
        )
        if latest_year is None:
            raise DemandRunError("No population loaded; run `make ingest-population` first")
        for cell, population, snaps in (
            await session.execute(
                select(
                    H3CellFeature.h3, H3CellFeature.population, H3CellFeature.snapshot_ids
                ).where(
                    H3CellFeature.scenario == "observed",
                    H3CellFeature.year == latest_year,
                    H3CellFeature.population.is_not(None),
                )
            )
        ).all():
            if cell in index:
                features["population"][index[cell]] = population
                snapshots.update(snaps)

    poi_features = {
        name: set(cfg["categories"])
        for name, cfg in feature_cfg.items()
        if cfg.get("from") == "osm_poi"
    }
    if poi_features:
        pois = (
            await session.execute(text("select category, ST_Y(geom), ST_X(geom) from poi"))
        ).all()
        for category, lat, lng in pois:
            i = index.get(h3.latlng_to_cell(lat, lng, res))
            if i is None:
                continue
            for name, categories in poi_features.items():
                if category in categories:
                    features[name][i] += 1
        osm_snapshot = await session.scalar(
            select(DatasetSnapshot.id)
            .where(DatasetSnapshot.source_id == "osm", DatasetSnapshot.granularity == "feature")
            .order_by(DatasetSnapshot.retrieved_at.desc())
            .limit(1)
        )
        if osm_snapshot:
            snapshots.add(osm_snapshot)
    return features, sorted(snapshots)


async def _catchment_mask(
    session: AsyncSession, cells: list[str], region_id: str, boundary_ids: list[int]
) -> NDArray[np.bool_]:
    inside = set(
        (
            await session.execute(
                text(
                    "select c.h3::text from region_cell rc join h3_cell c on c.h3 = rc.h3 "
                    "join admin_boundary b on ST_Within(ST_Centroid(c.geom), b.geom) "
                    "where rc.region_id = :region and b.osm_id = any(:ids)"
                ),
                {"region": region_id, "ids": boundary_ids},
            )
        ).scalars()
    )
    return np.array([c in inside for c in cells])


async def _existing_supply(
    session: AsyncSession,
    lats: NDArray[np.float64],
    lngs: NDArray[np.float64],
    assumptions: SupplyAssumptions,
    catchment_minutes: int,
) -> tuple[list[StationSupply], list[list[int]], list[uuid.UUID]]:
    stations = (
        await session.execute(
            select(
                ChargingStation.id, func.ST_Y(ChargingStation.geom), func.ST_X(ChargingStation.geom)
            )
        )
    ).all()
    evses: dict[uuid.UUID, list[tuple[float | None, int]]] = defaultdict(list)
    for station_id, kw, count in (
        await session.execute(select(Evse.station_id, Evse.max_kw, Evse.count))
    ).all():
        evses[station_id].append((kw, count))

    router = ValhallaRoutingProvider()
    points = [ShapelyPoint(x, y) for x, y in zip(lngs, lats, strict=True)]
    supplies, catchments = [], []
    for station_id, lat, lng in stations:
        supplies.append(station_supply(str(station_id), evses.get(station_id, []), assumptions))
        polygons = await router.isochrone(Point(lat=lat, lng=lng), [catchment_minutes], Mode.DRIVE)
        shapes = [prep(ShapelyPolygon(p.coordinates[0], p.coordinates[1:])) for p in polygons]
        catchments.append([i for i, pt in enumerate(points) if any(s.contains(pt) for s in shapes)])

    snapshot_ids = sorted(
        set((await session.execute(select(ChargerSourceRecord.snapshot_id).distinct())).scalars())
    )
    return supplies, catchments, snapshot_ids


def _r(x: float) -> float:
    return round(float(x), 3)


async def run_demand(
    session: AsyncSession,
    *,
    multiplier: float = 1.0,
    horizon_year: int = 2035,
    n_bootstrap: int = 200,
    seed: int = 20260925,
    region_id: str | None = None,
) -> DemandRunSummary:
    region_id = region_id or default_region_id()
    region = get_region(region_id)
    run_id = uuid.uuid4()
    a = Assumptions()
    segments_cfg = load_config("demand", "segments.yaml")
    alloc_cfg = load_config("demand", "allocation.yaml")
    supply_cfg = load_config("demand", "supply.yaml")
    res = int(alloc_cfg["h3_res"])

    series, start, end, sources, reg_snapshots = await _load_registrations(session)
    # Only this region's RTOs; others belong to regions they sit in.
    series = {k: v for k, v in series.items() if k[0] in region.rtos}
    if not series:
        raise DemandRunError(f"No registrations for {region_id}'s RTOs {list(region.rtos)}")
    synthetic = any(s.startswith("synthetic") for s in sources)
    segments = sorted({seg for _, seg in series})
    unknown = [s for s in segments if s not in segments_cfg["segments"]]
    if unknown:
        raise DemandRunError(f"No usage parameters for segments {unknown} in segments.yaml")

    usage = {}
    scrappage = {}
    for seg in segments:
        cfg = segments_cfg["segments"][seg]
        k = f"segments.{seg}"
        usage[seg] = SegmentUsage(
            annual_km=a.get(cfg["annual_km"], f"{k}.annual_km"),
            kwh_per_km=a.get(cfg["kwh_per_km"], f"{k}.kwh_per_km"),
            public_share=a.get(cfg["public_charging_share"], f"{k}.public_charging_share"),
            kwh_per_public_session=a.get(
                cfg["kwh_per_public_session"], f"{k}.kwh_per_public_session"
            ),
        )
        scrappage[seg] = a.get(cfg["scrappage_rate_annual"], f"{k}.scrappage_rate_annual")
    case_multiplier = {
        scenario_name(c, multiplier, region_id): a.get(
            segments_cfg["adoption_cases"][c], f"adoption_cases.{c}"
        )
        * multiplier
        for c in CASES
    }
    supply_assumptions = SupplyAssumptions(
        availability=a.get(supply_cfg["availability"], "supply.availability"),
        target_utilisation=a.get(supply_cfg["target_utilisation"], "supply.target_utilisation"),
        assumed_kw_when_unknown=a.get(
            supply_cfg["assumed_kw_when_unknown"], "supply.assumed_kw_when_unknown"
        ),
        assumed_charge_points_when_unknown=int(
            a.get(
                supply_cfg["assumed_charge_points_when_unknown"],
                "supply.assumed_charge_points_when_unknown",
            )
        ),
        new_charge_point_kw=a.get(supply_cfg["new_charge_point_kw"], "supply.new_charge_point_kw"),
    )
    catchment_minutes = int(a.get(supply_cfg["catchment_minutes"], "supply.catchment_minutes"))

    n_obs = _month_index(end, start) + 1
    horizon_months = _month_index(date(horizon_year, 12, 1), start) + 1
    history_years = list(range(start.year, end.year + 1))
    forecast_years = list(range(end.year, horizon_year + 1))

    def year_index(year: int, observed_only: bool) -> int:
        i = _month_index(date(year, 12, 1), start)
        return min(i, n_obs - 1) if observed_only else i

    history = scenario_key(region_id, "history")

    # --- cells, features, catchments ------------------------------------------
    cells, lats, lngs = await _region_cells(session, region_id, res)
    n_cells = len(cells)
    features, feature_snapshots = await _features(session, cells, res, alloc_cfg["features"])
    rtos = sorted({rto for rto, _ in series})
    masks = {}
    for rto in rtos:
        catchment = alloc_cfg["rto_catchments"].get(rto)
        if catchment is None:
            raise DemandRunError(f"RTO {rto} has no catchment in config/demand/allocation.yaml")
        masks[rto] = await _catchment_mask(session, cells, region_id, catchment["boundaries"])
        if not masks[rto].any():
            raise DemandRunError(f"RTO {rto}'s catchment contains no cells of region {region_id}")

    # --- forecasts, allocated to cells --------------------------------------------
    # stock[scenario][year][segment] -> (3, n_cells) array of p10/p50/p90
    stock: dict[str, dict[int, dict[str, NDArray[np.float64]]]] = defaultdict(
        lambda: defaultdict(lambda: {s: np.zeros((3, n_cells)) for s in segments})
    )
    fit_evidence = []
    for i, (rto, seg) in enumerate(sorted(series)):
        paths = forecast_paths(series[(rto, seg)], horizon_months, n_bootstrap, seed + i)
        weights = alloc_cfg["segment_weights"][seg]
        mask = masks[rto]
        shares = np.zeros(n_cells)
        shares[mask] = allocation_shares(
            {f: features[f][mask] for f in weights}, weights, int(mask.sum())
        )
        observed = stock_quantiles(paths, scrappage[seg], 1.0)
        for year in history_years:
            j = year_index(year, observed_only=True)
            stock[history][year][seg] += np.outer(
                [observed.p10[j], observed.p50[j], observed.p90[j]], shares
            )
        for scenario, mult in case_multiplier.items():
            q = stock_quantiles(paths, scrappage[seg], mult)
            for year in forecast_years:
                j = year_index(year, observed_only=False)
                stock[scenario][year][seg] += np.outer([q.p10[j], q.p50[j], q.p90[j]], shares)
        fit_evidence.append((rto, seg, paths.method, paths.params, paths.rmse))

    # --- supply & gap, write rows ----------------------------------------------------
    supplies, catchments, charger_snapshots = await _existing_supply(
        session, lats, lngs, supply_assumptions, catchment_minutes
    )
    station_kwh = [s.kwh_per_day for s in supplies]
    input_snapshots = sorted(set(reg_snapshots) | set(feature_snapshots) | set(charger_snapshots))

    scenarios = [history, *case_multiplier]
    await session.execute(
        delete(H3CellFeature).where(
            H3CellFeature.scenario.in_(scenarios),
            H3CellFeature.h3.in_(select(RegionCell.h3).where(RegionCell.region_id == region_id)),
        )
    )

    region_totals: dict[str, dict[int, dict[str, float]]] = defaultdict(dict)
    rows: list[dict[str, Any]] = []
    for scenario, by_year in stock.items():
        for year, by_seg in sorted(by_year.items()):
            total = np.sum([by_seg[s] for s in segments], axis=0)  # (3, n_cells)
            kwh = np.sum([public_kwh_per_day(by_seg[s], usage[s]) for s in segments], axis=0)
            sessions = np.sum(
                [
                    sessions_per_day(public_kwh_per_day(by_seg[s][1], usage[s]), usage[s])
                    for s in segments
                ],
                axis=0,
            )
            gap = None if scenario == history else two_step_fca(kwh[1], station_kwh, catchments)
            for c in range(n_cells):
                rows.append(
                    {
                        "h3": cells[c],
                        "year": year,
                        "scenario": scenario,
                        "ev_stock_p10": _r(total[0, c]),
                        "ev_stock_p50": _r(total[1, c]),
                        "ev_stock_p90": _r(total[2, c]),
                        "ev_stock_by_segment": {
                            s: [_r(v) for v in by_seg[s][:, c]] for s in segments
                        },
                        "public_kwh_p10": _r(kwh[0, c]),
                        "public_kwh_p50": _r(kwh[1, c]),
                        "public_kwh_p90": _r(kwh[2, c]),
                        "sessions_p50": _r(sessions[c]),
                        "access_ratio": _r(gap.access_ratio[c]) if gap else None,
                        "served_kwh": _r(gap.served_kwh[c]) if gap else None,
                        "gap_kwh": _r(gap.gap_kwh[c]) if gap else None,
                        "snapshot_ids": input_snapshots,
                    }
                )
            region_totals[scenario][year] = {
                "ev_stock_p10": float(total[0].sum()),
                "ev_stock_p50": float(total[1].sum()),
                "ev_stock_p90": float(total[2].sum()),
                "public_kwh_p50": float(kwh[1].sum()),
                "public_kwh_p10": float(kwh[0].sum()),
                "public_kwh_p90": float(kwh[2].sum()),
                **(
                    {
                        "gap_kwh": float(gap.gap_kwh.sum()),
                        "additional_charge_points": float(
                            additional_charge_points(float(gap.gap_kwh.sum()), supply_assumptions)
                        ),
                    }
                    if gap
                    else {}
                ),
            }
            # Written per scenario-year: holding every row for a large region (the
            # corridor is ~860k) at once needs several GB of Python objects.
            for chunk in range(0, len(rows), 5000):
                await session.execute(insert(H3CellFeature), rows[chunk : chunk + 5000])
            rows.clear()

    # --- evidence ------------------------------------------------------------------------
    base_conf = "NONE" if synthetic else "MEDIUM"
    derived_conf = "NONE" if synthetic else "LOW"
    config_record = {
        "method": METHOD,
        "region_id": region_id,
        "history_scenario": history,
        "registration_sources": sorted(sources),
        "observed_window": [start.isoformat(), end.isoformat()],
        "scenarios": case_multiplier,
        "what_if_multiplier": multiplier,
        "horizon_year": horizon_year,
        "n_bootstrap": n_bootstrap,
        "seed": seed,
        "h3_res": res,
        "allocation_weights": alloc_cfg["segment_weights"],
        "allocation_status": alloc_cfg.get("status"),
        "rto_catchments": alloc_cfg["rto_catchments"],
        "catchment_minutes": catchment_minutes,
        "placeholders_in_use": a.placeholders_in_use,
        "sourced_parameters": a.sources,
        "stations": len(supplies),
        "stations_with_assumed_count": sum(s.assumed_count for s in supplies),
        "charge_points_with_assumed_kw": sum(s.assumed_kw_charge_points for s in supplies),
        "unused_station_supply_note": (
            "stations whose catchment has no modelled demand add no supply"
        ),
        "git_sha": os.environ.get("GIT_SHA", "unknown"),
    }
    evidence: list[Evidence] = [
        Evidence(
            subject_type="demand_run",
            subject_id=str(run_id),
            metric="config",
            value_text=json.dumps(config_record, default=str),
            method=METHOD,
            snapshot_ids=input_snapshots,
            confidence=base_conf,
            run_id=run_id,
        )
    ]
    for rto, seg, method, params, rmse in fit_evidence:
        for metric, value in (
            ("bass_m", params.m if params else None),
            ("bass_p", params.p if params else None),
            ("bass_q", params.q if params else None),
            ("fit_rmse_monthly_registrations", rmse),
        ):
            evidence.append(
                Evidence(
                    subject_type="adoption_fit",
                    subject_id=f"{rto}:{seg}",
                    metric=metric,
                    value_num=value,
                    method=method,
                    snapshot_ids=reg_snapshots,
                    confidence=base_conf,
                    run_id=run_id,
                )
            )
    for scenario, totals_by_year in region_totals.items():
        for year, totals in totals_by_year.items():
            for metric, unit, conf in (
                ("ev_stock", "vehicles", base_conf),
                ("public_kwh", "kWh/day", derived_conf),
                ("gap_kwh", "kWh/day", derived_conf),
                ("additional_charge_points", "charge points", derived_conf),
            ):
                key = f"{metric}_p50" if metric in ("ev_stock", "public_kwh") else metric
                if key not in totals:
                    continue
                evidence.append(
                    Evidence(
                        subject_type="region",
                        subject_id=region_id,
                        metric=f"{metric}:{scenario}:{year}",
                        value_num=totals[key],
                        p10=totals.get(f"{metric}_p10"),
                        p90=totals.get(f"{metric}_p90"),
                        unit=unit,
                        method=METHOD,
                        snapshot_ids=input_snapshots,
                        confidence=conf,
                        run_id=run_id,
                    )
                )
    session.add_all(evidence)
    await session.commit()

    return DemandRunSummary(
        run_id=str(run_id),
        scenarios=scenarios,
        years=sorted(set(history_years) | set(forecast_years)),
        cells=n_cells,
        registration_source=", ".join(sorted(sources)),
        observed_through=f"{end:%Y-%m}",
        placeholders_in_use=a.placeholders_in_use,
        region_totals={s: dict(v) for s, v in region_totals.items()},
    )

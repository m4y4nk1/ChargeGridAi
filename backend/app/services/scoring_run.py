"""Planning-run steps for Phase 5: catchments -> scoring (Section 9.6).

Each feasible candidate gets a Valhalla drive-time catchment (the same minutes
as the Phase 3 supply model), the H3 cells inside it, and raw measurements for
the eight sub-scores. Sub-scores are percentile ranks within the run's feasible
candidates; the weighted total, rank and rank robustness use the scenario's
weight profile. Other profiles are re-ranked on request from the stored
sub-scores (`rank_with`), without re-measuring anything.
"""

from __future__ import annotations

import asyncio
import json
import math
import uuid
from collections import Counter, defaultdict
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

import h3
import numpy as np
import yaml
from geoalchemy2.shape import from_shape
from shapely.geometry import MultiPolygon
from shapely.geometry import Polygon as ShapelyPolygon
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.assumptions import Assumptions, load_config
from app.core.settings import get_settings
from app.db.models.charging import Evse
from app.db.models.planning import PlanningRun, RunSite, SiteCatchment, SiteFeature
from app.db.models.provenance import Evidence
from app.db.repositories.demand import supply_assumptions
from app.engines.scoring import (
    SUB_SCORES,
    cagr,
    index_vs_benchmark,
    percentile_rank,
    rank_order,
    rank_robustness,
    score_matrix,
    totals,
    validate_weights,
)
from app.engines.supply_gap import station_supply
from app.providers.base import Mode, Point
from app.providers.valhalla.routing import ValhallaRoutingProvider

METHOD = "scoring_v1"
H3_RES = 8
MAX_FORECAST_YEAR = 2035
CONFIDENCE_ORDER = ("NONE", "LOW", "MEDIUM", "HIGH")

Progress = Callable[..., Awaitable[None]]


# --- weight profiles -----------------------------------------------------------------


def weight_profiles() -> dict[str, dict[str, Any]]:
    """config/weights/*.yaml keyed by profile name."""
    profiles = {}
    for path in sorted(get_settings().config_dir.joinpath("weights").glob("*.yaml")):
        data = yaml.safe_load(path.read_text())
        profiles[data["name"]] = {
            "name": data["name"],
            "label": data.get("label", data["name"]),
            "version": data.get("version"),
            "weights": validate_weights(data["weights"]),
        }
    return profiles


def scoring_config() -> dict[str, Any]:
    return load_config("scoring.yaml")


# --- ranking (also used by the API for other profiles) -----------------------------------


@dataclass(frozen=True)
class Ranked:
    index: int
    rank: int
    total: float
    robustness: float


def rank_with(
    sub_scores: Sequence[dict[str, float]],
    weights: dict[str, float],
    top_n: int,
    robustness_cfg: dict[str, Any],
) -> list[Ranked]:
    """Rank sites best-first under `weights`; robustness = share of draws in the top N."""
    m = score_matrix(sub_scores)
    total = totals(m, weights)
    order = rank_order(total)
    robust = rank_robustness(
        m,
        weights,
        top_n=top_n,
        draws=int(robustness_cfg["draws"]),
        concentration=float(robustness_cfg["concentration"]),
        seed=int(robustness_cfg["seed"]),
    )
    return [
        Ranked(int(i), r + 1, round(float(total[i]), 2), round(float(robust[i]), 3))
        for r, i in enumerate(order)
    ]


# --- catchments ----------------------------------------------------------------------


def _isochrone_shape(polygons: Sequence[Any]) -> MultiPolygon | None:
    parts = []
    for p in polygons:
        if not p.coordinates or len(p.coordinates[0]) < 4:
            continue
        shape = ShapelyPolygon(p.coordinates[0], p.coordinates[1:])
        parts.append(shape if shape.is_valid else shape.buffer(0))
    polys = [g for part in parts for g in getattr(part, "geoms", [part]) if not g.is_empty]
    return MultiPolygon(polys) if polys else None


def _cells_in(shape: MultiPolygon, lat: float, lng: float) -> list[str]:
    cells: set[str] = {h3.latlng_to_cell(lat, lng, H3_RES)}
    for poly in shape.geoms:
        outer = [(y, x) for x, y in poly.exterior.coords]
        holes = [[(y, x) for x, y in ring.coords] for ring in poly.interiors]
        cells.update(h3.polygon_to_cells(h3.LatLngPoly(outer, *holes), H3_RES))
    return sorted(cells)


async def _catchments(
    sites: Sequence[tuple[uuid.UUID, float, float]], minutes: int, concurrency: int
) -> dict[uuid.UUID, tuple[MultiPolygon, list[str]] | str]:
    """site_id -> (shape, cells), or an error string when routing failed."""
    router = ValhallaRoutingProvider()
    sem = asyncio.Semaphore(concurrency)

    async def one(site_id: uuid.UUID, lat: float, lng: float) -> tuple[uuid.UUID, Any]:
        async with sem:
            try:
                polygons = await router.isochrone(Point(lat=lat, lng=lng), [minutes], Mode.DRIVE)
            except Exception as exc:  # routing failure is per site, not per run
                return site_id, f"isochrone failed: {type(exc).__name__}"
        shape = _isochrone_shape(polygons)
        if shape is None:
            return site_id, "isochrone empty (site not on the routable network)"
        return site_id, (shape, _cells_in(shape, lat, lng))

    results = await asyncio.gather(*(one(*s) for s in sites))
    return dict(results)


# --- measurements --------------------------------------------------------------------

_TRAFFIC_SQL = text(
    # Bounding-box prefilter on the indexed geometry (1 km is under 0.0105 degrees
    # here), then exact clipping in metres in UTM 43N.
    "select s.id, coalesce(sum(ST_Length(ST_Intersection(ST_Transform(r.geom, 32643), "
    "  ST_Buffer(ST_Transform(s.geom, 32643), :radius)))), 0) as major_road_m "
    "from candidate_site s left join road_segment r on r.class = any(:classes) "
    "  and r.geom && ST_Expand(s.geom, :radius / 95000.0) "
    "  and ST_DWithin(ST_Transform(r.geom, 32643), ST_Transform(s.geom, 32643), :radius) "
    "where s.id = any(:ids) group by s.id"
)

_PARKING_SQL = text(
    "select s.id from candidate_site s where s.id = any(:ids) and exists ("
    "  select 1 from poi p where p.category = 'PARKING' "
    "  and ST_DWithin(p.geom::geography, s.geom::geography, :within))"
)

_POI_COUNTS_SQL = text(
    "select c.site_id, p.category, count(*) from site_catchment c "
    "join poi p on ST_Intersects(c.geom, p.geom) "
    "where c.site_id = any(:ids) and p.category <> 'EV_CHARGER' group by 1, 2"
)

_STATIONS_SQL = text(
    "select c.site_id, st.id from site_catchment c "
    "join charging_station st on ST_Intersects(c.geom, st.geom) where c.site_id = any(:ids)"
)

_CELLS_SQL = text(
    "select f.h3::text, f.year, f.public_kwh_p50, f.ev_stock_p50, f.access_ratio "
    "from h3_cell_feature f join region_cell c on c.h3 = f.h3 "
    "where c.region_id = :region and f.scenario = :scenario and f.year = any(:years)"
)

_POPULATION_SQL = text(
    "select f.h3::text, f.population from h3_cell_feature f join region_cell c on c.h3 = f.h3 "
    "where c.region_id = :region and f.scenario = 'observed' and f.population is not null"
)


def _growth_window(target_year: int, horizon: int) -> tuple[int, int, str | None]:
    end = min(target_year + horizon, MAX_FORECAST_YEAR)
    if end > target_year:
        note = (
            None
            if end == target_year + horizon
            else f"forecast ends {MAX_FORECAST_YEAR}; growth measured {target_year}-{end}"
        )
        return target_year, end, note
    start = target_year - horizon
    return start, target_year, f"forecast ends {MAX_FORECAST_YEAR}; growth measured {start}-{end}"


def _min_confidence(levels: Sequence[str]) -> str:
    return min(levels, key=CONFIDENCE_ORDER.index) if levels else "NONE"


def _sum(values: dict[str, float | None], cells: Sequence[str]) -> float:
    return float(sum(values.get(c) or 0.0 for c in cells))


async def score_run(
    session: AsyncSession,
    run: PlanningRun,
    region_id: str,
    target_year: int,
    demand_scenario: str,
    demand_synthetic: bool,
    weight_profile: str,
    snapshot_ids: list[uuid.UUID],
    a: Assumptions,
    progress: Progress,
) -> dict[str, Any]:
    """Run the catchments and scoring steps; returns the funnel's scoring entries."""
    cfg = scoring_config()
    profiles = weight_profiles()
    if weight_profile not in profiles:
        raise ValueError(f"unknown weight profile '{weight_profile}'; use {sorted(profiles)}")
    weights = profiles[weight_profile]["weights"]
    supply_cfg = load_config("demand", "supply.yaml")
    minutes = int(a.get(supply_cfg["catchment_minutes"], "supply.catchment_minutes"))

    # --- catchments
    await progress(session, run, "catchments", "started")
    rows = (
        await session.execute(
            text(
                "select id, ST_Y(geom), ST_X(geom), host_type from candidate_site "
                "where created_by_run = :run and status = 'feasible'"
            ),
            {"run": run.id},
        )
    ).all()
    sites = [(r[0], float(r[1]), float(r[2])) for r in rows]
    host_type = {r[0]: r[3] for r in rows}
    catchments = await _catchments(sites, minutes, int(cfg["catchment"]["isochrone_concurrency"]))
    for site_id, result in catchments.items():
        if isinstance(result, str):
            continue
        shape, cells = result
        session.add(
            SiteCatchment(
                site_id=site_id,
                minutes=minutes,
                geom=from_shape(shape, srid=4326),
                h3_cells=cells,
            )
        )
    await session.flush()
    failed = {sid: r for sid, r in catchments.items() if isinstance(r, str)}
    await progress(
        session,
        run,
        "catchments",
        "completed",
        catchments=len(sites) - len(failed),
        failed=len(failed),
    )

    # --- scoring
    await progress(session, run, "scoring", "started")
    scored_ids = [sid for sid, _, _ in sites if sid not in failed]
    ids = list(scored_ids)

    y0, y1, growth_note = _growth_window(target_year, int(cfg["future_growth"]["horizon_years"]))
    kwh: dict[int, dict[str, float | None]] = defaultdict(dict)
    evs: dict[str, float | None] = {}
    access: dict[str, float | None] = {}
    for h, year, k, ev, ar in (
        await session.execute(
            _CELLS_SQL,
            {
                "region": region_id,
                "scenario": demand_scenario,
                "years": sorted({target_year, y0, y1}),
            },
        )
    ).all():
        kwh[year][h] = k
        if year == target_year:
            evs[h], access[h] = ev, ar
    population = {
        h: p for h, p in (await session.execute(_POPULATION_SQL, {"region": region_id})).all()
    }

    features = {
        sid: f
        for sid, f in (
            await session.execute(
                select(SiteFeature.site_id, SiteFeature.features).where(
                    SiteFeature.site_id.in_(ids)
                )
            )
        ).all()
    }
    traffic_cfg = cfg["traffic"]
    major_road_m = {
        sid: float(m)
        for sid, m in (
            await session.execute(
                _TRAFFIC_SQL,
                {
                    "ids": ids,
                    "radius": traffic_cfg["radius_m"],
                    "classes": traffic_cfg["major_road_classes"],
                },
            )
        ).all()
    }
    land_cfg = cfg["land"]
    near_parking = set(
        (
            await session.execute(
                _PARKING_SQL, {"ids": ids, "within": land_cfg["parking_within_m"]}
            )
        ).scalars()
    )
    poi_counts: dict[uuid.UUID, dict[str, int]] = defaultdict(dict)
    for sid, category, n in (await session.execute(_POI_COUNTS_SQL, {"ids": ids})).all():
        poi_counts[sid][category] = int(n)

    supply_a, supply_placeholders = supply_assumptions()
    for p in supply_placeholders:
        a.placeholders.add(p)
    evses: dict[uuid.UUID, list[tuple[float | None, int]]] = defaultdict(list)
    for station_id, kw, count in (
        await session.execute(select(Evse.station_id, Evse.max_kw, Evse.count))
    ).all():
        evses[station_id].append((kw, count))
    station_kwh: dict[uuid.UUID, float] = {}
    station_cps: dict[uuid.UUID, int] = {}
    for station_id in {s for s in evses} | set(
        (await session.execute(text("select id from charging_station"))).scalars()
    ):
        supply = station_supply(str(station_id), evses.get(station_id, []), supply_a)
        station_kwh[station_id], station_cps[station_id] = supply.kwh_per_day, supply.charge_points
    stations_in: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    for sid, station_id in (await session.execute(_STATIONS_SQL, {"ids": ids})).all():
        stations_in[sid].append(station_id)

    suitability = {
        host: a.get(node, f"scoring.land.host_suitability.{host}")
        for host, node in land_cfg["host_suitability"].items()
    }
    parking_bonus = a.get(land_cfg["parking_bonus"], "scoring.land.parking_bonus")
    underserved = a.get(
        cfg["equity"]["underserved_access_ratio"], "scoring.equity.underserved_access_ratio"
    )
    road_order: list[str] = cfg["accessibility"]["road_class_order"]

    # Regional benchmarks (= 100) over every modelled cell.
    region_cells = list(kwh[target_year])
    n_region = max(len(region_cells), 1)
    region_pop = _sum(population, region_cells)
    region_evs = _sum(evs, region_cells)
    region_kwh = _sum(kwh[target_year], region_cells)
    region_cps = float(sum(station_cps.values()))
    bench = {
        "population_density": region_pop / n_region,
        "evs_per_1000_people": 1000 * region_evs / region_pop if region_pop else None,
        "demand_density": region_kwh / n_region,
        "charge_points_per_1000_evs": 1000 * region_cps / region_evs if region_evs else None,
    }

    raw: dict[str, dict[str, float]] = {}
    details: dict[uuid.UUID, dict[str, Any]] = {}
    for sid in ids:
        shape_cells = catchments[sid]
        assert not isinstance(shape_cells, str)
        cells = [c for c in shape_cells[1] if c in kwh[target_year]]
        f = features.get(sid, {})
        demand = _sum(kwh[target_year], cells)
        pop = _sum(population, cells)
        ev = _sum(evs, cells)
        start, end = _sum(kwh[y0], cells), _sum(kwh[y1], cells)
        growth = cagr(start, end, y1 - y0)
        supply_kwh = sum(station_kwh.get(s, 0.0) for s in stations_in[sid])
        cps = sum(station_cps.get(s, 0) for s in stations_in[sid])
        under_pop = sum(
            population.get(c) or 0.0
            for c in cells
            if access.get(c) is not None and (access[c] or 0.0) < underserved
        )
        road_class = f.get("nearest_road_class")
        road_rank = (
            len(road_order) - road_order.index(road_class) if road_class in road_order else 0
        )
        host = host_type[sid]
        land = suitability.get(host, suitability.get("ROADSIDE", 0.0))
        parking_nearby = sid in near_parking and host != "PARKING"
        if parking_nearby:
            land += parking_bonus
        substation_m = f.get("nearest_substation_m")
        raw[str(sid)] = {
            "demand": demand,
            "road_rank": float(road_rank),
            "catchment_population": pop,
            "traffic": major_road_m.get(sid, 0.0),
            "grid": float(substation_m) if substation_m is not None else math.inf,
            "land": land,
            "future_growth": growth if growth is not None else -math.inf,
            "competition_gap": (1.0 - min(1.0, supply_kwh / demand)) if demand > 0 else 0.0,
            "equity": under_pop / pop if pop > 0 else 0.0,
        }
        n_cells = max(len(cells), 1)
        details[sid] = {
            "raw": {
                "demand_kwh_per_day": round(demand, 2),
                "nearest_road_class": road_class,
                "catchment_population": round(pop),
                "major_road_m_within_radius": round(major_road_m.get(sid, 0.0)),
                "nearest_substation_m": substation_m,
                "host_type": host,
                "host_suitability": suitability.get(host),
                "parking_nearby": parking_nearby,
                "land_score_input": round(land, 3),
                "demand_cagr": None if growth is None else round(growth, 4),
                "growth_window": [y0, y1],
                "existing_supply_kwh_per_day": round(supply_kwh, 1),
                "competition_gap": round(raw[str(sid)]["competition_gap"], 4),
                "underserved_population_share": round(raw[str(sid)]["equity"], 4),
            },
            "catchment": {
                "minutes": minutes,
                "cells": len(cells),
                "population": round(pop),
                "evs": round(ev, 1),
                "public_kwh_per_day": round(demand, 2),
                "existing_stations": len(stations_in[sid]),
                "existing_charge_points": cps,
            },
            "indices": {
                "population_density": index_vs_benchmark(
                    pop / n_cells, bench["population_density"]
                ),
                "evs_per_1000_people": index_vs_benchmark(
                    1000 * ev / pop if pop else None, bench["evs_per_1000_people"]
                ),
                "demand_density": index_vs_benchmark(demand / n_cells, bench["demand_density"]),
                "charge_points_per_1000_evs": index_vs_benchmark(
                    1000 * cps / ev if ev else None, bench["charge_points_per_1000_evs"]
                ),
                # No source ingested yet; shown as unavailable rather than estimated.
                "income": None,
                "home_charging_access": None,
            },
            "poi_counts": poi_counts.get(sid, {}),
        }

    keys = list(raw)
    col = {k: [raw[s][k] for s in keys] for k in next(iter(raw.values()), {})}
    pct: dict[str, np.ndarray] = {}
    if keys:
        pct = {
            "demand": percentile_rank(col["demand"]),
            "accessibility": (
                percentile_rank(col["road_rank"]) + percentile_rank(col["catchment_population"])
            )
            / 2,
            "traffic": percentile_rank(col["traffic"]),
            "grid": percentile_rank(col["grid"], higher_is_better=False),
            "land": percentile_rank(col["land"]),
            "future_growth": percentile_rank(col["future_growth"]),
            "competition_gap": percentile_rank(col["competition_gap"]),
            "equity": percentile_rank(col["equity"]),
        }
    sub_scores = [{k: round(float(pct[k][i]), 2) for k in SUB_SCORES} for i in range(len(keys))]
    # A sub-score whose inputs are identical for every site scores everyone 50: it can't
    # change the ranking, but it inflates totals, so the run says so.
    sources = {
        "accessibility": ["road_rank", "catchment_population"],
        **{k: [k] for k in SUB_SCORES if k != "accessibility"},
    }
    uninformative = {
        k: "identical for every candidate in this run"
        for k, cols in sources.items()
        if keys and all(len(set(col[c])) == 1 for c in cols)
    }
    top_n = int(cfg["top_n"])
    ranked = rank_with(sub_scores, weights, top_n, cfg["robustness"])
    shortlist = min(top_n * int(cfg["shortlist_multiple"]), len(keys))

    synth = "NONE" if demand_synthetic else "LOW"
    confidence = {
        "demand": synth,
        "accessibility": "LOW",  # WorldPop population is LOW confidence
        "traffic": "LOW",  # proxy: major road length, no traffic data
        "grid": "LOW",  # proxy: distance to OSM substation
        "land": "LOW" if any(p.startswith("scoring.land") for p in a.placeholders) else "MEDIUM",
        "future_growth": synth,
        "competition_gap": synth,
        "equity": synth,
    }
    total_confidence = _min_confidence([confidence[k] for k in SUB_SCORES if weights[k] > 0])

    evidence = []
    for r in ranked:
        sid = uuid.UUID(keys[r.index])
        subs = sub_scores[r.index]
        session.add(
            RunSite(
                run_id=run.id,
                site_id=sid,
                rank=r.rank,
                score_total=r.total,
                sub_scores=subs,
                robustness=r.robustness,
                detail=details[sid],
            )
        )
        evidence.append(
            Evidence(
                subject_type="candidate_site",
                subject_id=str(sid),
                metric="score:total",
                value_num=r.total,
                value_text=json.dumps(
                    {
                        "profile": weight_profile,
                        "rank": r.rank,
                        "sub_scores": subs,
                        "robustness_top_n": top_n,
                        "robustness": r.robustness,
                    }
                ),
                unit="0-100",
                method=METHOD,
                snapshot_ids=snapshot_ids,
                confidence=total_confidence,
                run_id=run.id,
            )
        )
    for sid, reason in failed.items():
        session.add(RunSite(run_id=run.id, site_id=sid, detail={"unscored": reason}))
    session.add_all(evidence)
    await progress(session, run, "scoring", "completed", scored=len(ranked))

    run.inputs = {
        **run.inputs,
        "scoring": json.loads(
            json.dumps(
                {
                    "method": METHOD,
                    "weight_profile": weight_profile,
                    "weights": weights,
                    "top_n": top_n,
                    "shortlist": shortlist,
                    "robustness": cfg["robustness"],
                    "catchment_minutes": minutes,
                    "growth_window": [y0, y1],
                    "growth_note": growth_note,
                    "confidence": confidence,
                    "total_confidence": total_confidence,
                    "uninformative": uninformative,
                    "benchmarks": bench,
                    "config": cfg,
                },
                default=str,
            )
        ),
        "placeholders_in_use": a.placeholders_in_use,
    }
    return {
        "shortlisted": shortlist,
        "scored": {
            "total": len(ranked),
            "unscored": len(failed),
            "unscored_reasons": dict(Counter(failed.values())),
            "weight_profile": weight_profile,
            "top_n": top_n,
        },
    }

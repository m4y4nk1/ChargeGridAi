"""Per-region data readiness scores (Phase 12 acceptance).

Each dimension is checked against the platform's own data: what exists inside the
region's bounding box or cells, whether it is real, synthetic or a placeholder, and
whether routing covers the region. The result says what's missing and the command
that fixes it, so onboarding a new region is a checklist rather than guesswork.
"""

from __future__ import annotations

from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.assumptions import load_config
from app.core.regions import Region, all_regions, scenario_key
from app.core.settings import get_settings


def _grade(score: float, grades: dict[str, float]) -> str:
    return next(g for g, lo in sorted(grades.items(), key=lambda kv: -kv[1]) if score >= lo)


async def _count(session: AsyncSession, sql: str, params: dict[str, Any]) -> int:
    return int((await session.execute(text(sql), params)).scalar() or 0)


async def _routable(region: Region) -> bool:
    """Valhalla can snap the region centre to its graph."""
    lat, lng = region.bbox.centre
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"{get_settings().valhalla_url}/locate",
                json={"locations": [{"lat": lat, "lon": lng}], "costing": "auto"},
            )
        return r.status_code == 200 and bool(r.json() and r.json()[0].get("edges"))
    except (httpx.HTTPError, ValueError, IndexError, KeyError):
        return False


def _placeholder_share(*parts: tuple[str, ...]) -> float:
    """Share of {value, illustrative} parameters still on the illustrative fallback."""
    total = placeholders = 0

    def walk(node: Any) -> None:
        nonlocal total, placeholders
        if isinstance(node, dict):
            if "illustrative" in node and "value" in node:
                total += 1
                placeholders += not isinstance(node["value"], int | float)
                return
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    for p in parts:
        walk(load_config(*p))
    return placeholders / total if total else 0.0


async def region_readiness(session: AsyncSession, region: Region) -> dict[str, Any]:
    cfg = load_config("readiness.yaml")
    b = region.bbox
    env = {"x0": b.min_lng, "y0": b.min_lat, "x1": b.max_lng, "y1": b.max_lat}
    box = "geom && ST_MakeEnvelope(:x0, :y0, :x1, :y1, 4326)"
    roads = await _count(session, f"select count(*) from road_segment where {box}", env)
    pois = await _count(session, f"select count(*) from poi where {box}", env)
    bounds = await _count(session, f"select count(*) from admin_boundary where {box}", env)
    cells = await _count(
        session, "select count(*) from region_cell where region_id = :r", {"r": region.id}
    )
    demand_rows = await _count(
        session,
        "select count(*) from (select 1 from h3_cell_feature where scenario = :s limit 1) x",
        {"s": scenario_key(region.id, "base")},
    )
    real_regs = synthetic_regs = 0
    if region.rtos:
        rows = (
            await session.execute(
                text(
                    "select sn.source_id, count(*) from vehicle_registration_agg v "
                    "join dataset_snapshot sn on sn.id = v.snapshot_id "
                    "where v.rto_code = any(:rtos) group by 1"
                ),
                {"rtos": list(region.rtos)},
            )
        ).all()
        for source, n in rows:
            if source.startswith("synthetic"):
                synthetic_regs += n
            else:
                real_regs += n
    stations = await _count(session, f"select count(*) from charging_station where {box}", env)
    sources = await _count(
        session,
        "select count(distinct l.source_id) from station_source_link l "
        f"join charging_station s on s.id = l.station_id where s.{box}",
        env,
    )
    substations = await _count(
        session, f"select count(*) from grid_asset where type = 'substation' and {box}", env
    )
    discom = await _count(session, f"select count(*) from grid_asset_rated where {box}", env)
    solar = await _count(
        session,
        "select count(*) from solar_resource where lat between :y0 and :y1 "
        "and lng between :x0 and :x1",
        env,
    )
    observed = await _count(
        session,
        "select count(distinct u.station_id) from station_utilisation_daily u "
        f"join charging_station s on s.id = u.station_id where s.{box}",
        env,
    )
    routable = await _routable(region)
    maharashtra = b.min_lat < 22.1 and b.max_lat > 15.6 and b.min_lng < 80.9 and b.max_lng > 72.6
    ph = _placeholder_share(("tariffs", "maharashtra.yaml"), ("costs", "unit_costs.yaml"))
    t = cfg["thresholds"]

    dims: dict[str, dict[str, Any]] = {
        "roads_pois": {
            "score": min(1.0, roads / t["min_road_segments"]) * 0.7
            + min(1.0, pois / t["min_pois"]) * 0.3,
            "detail": f"{roads} road segments, {pois} POIs",
            "fix": "make onboard-region REGION={id} PBF=<extract> (OSM import)",
        },
        "boundaries": {
            "score": 1.0 if bounds else 0.0,
            "detail": f"{bounds} admin boundaries",
            "fix": "include admin_level 4-6 relations in the OSM extract",
        },
        "routing": {
            "score": 1.0 if routable else 0.0,
            "detail": "Valhalla graph covers the region" if routable else "no routing graph",
            "fix": "add the region's extract to infra/docker/valhalla/custom_files and rebuild",
        },
        "population": {
            "score": 1.0 if cells else 0.0,
            "detail": f"{cells} H3 cells with population (WorldPop, confidence LOW)",
            "fix": "make ingest-population REGION={id}",
        },
        "registrations": {
            "score": 1.0 if real_regs else 0.25 if synthetic_regs else 0.0,
            "detail": (
                f"{real_regs} real VAHAN rows"
                if real_regs
                else f"synthetic only ({synthetic_regs} rows)"
                if synthetic_regs
                else "none" + ("" if region.rtos else "; RTO list not sourced yet")
            ),
            "fix": "import a VAHAN export (python -m app.ingestion.cli vahan <csv>)",
        },
        "demand": {
            "score": 1.0 if demand_rows else 0.0,
            "detail": "base forecast and gap exist" if demand_rows else "no demand run",
            "fix": "make demand REGION={id}",
        },
        "chargers": {
            "score": 0.0 if not stations else 1.0 if sources >= 2 else 0.6,
            "detail": f"{stations} stations from {sources} source(s)",
            "fix": "make ingest-ocm OCM_REGION={id}; import BEE/operator lists",
        },
        "grid": {
            "score": 1.0 if discom else 0.3 if substations else 0.0,
            "detail": (
                f"{discom} DISCOM-rated assets"
                if discom
                else f"OSM substations only ({substations}); proximity proxy"
            ),
            "fix": "make ingest-discom CSV=<file> DISCOM=<name> (data agreement)",
        },
        "solar": {
            "score": 1.0 if solar else 0.0,
            "detail": "NASA POWER irradiance loaded" if solar else "no irradiance for the region",
            "fix": "make ingest-solar (region centre)",
        },
        "tariffs_costs": {
            "score": (1.0 - ph) if maharashtra else 0.0,
            "detail": (
                f"{ph:.0%} of tariff/cost parameters still illustrative"
                if maharashtra
                else "no tariff file for this state"
            ),
            "fix": "source the MERC order and unit costs (Admin → Config)",
        },
        "operator_feeds": {
            "score": 1.0 if observed else 0.0,
            "detail": f"{observed} stations with observed utilisation",
            "fix": "configure an OCPI operator (config/ocpi_operators.yaml)",
        },
    }
    weights = cfg["weights"]
    score = 100 * sum(weights[k] * d["score"] for k, d in dims.items()) / sum(weights.values())
    for k, d in dims.items():
        d["score"] = round(d["score"], 2)
        d["weight"] = weights[k]
        d["fix"] = d["fix"].format(id=region.id)
    blockers = [
        k for k in ("roads_pois", "routing", "population", "demand") if dims[k]["score"] == 0
    ]
    return {
        "region_id": region.id,
        "name": region.name,
        "status": region.status,
        "score": round(score, 1),
        "grade": _grade(score, cfg["grades"]),
        "can_plan": region.onboarded and not blockers,
        "blockers": blockers,
        "dimensions": dims,
    }


async def all_readiness(session: AsyncSession) -> list[dict[str, Any]]:
    return [await region_readiness(session, r) for r in all_regions(include_planned=True)]

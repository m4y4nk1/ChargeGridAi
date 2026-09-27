"""catalog.*, gov.* (Data Discovery)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import text

from app.agents.tools.base import Tool, ToolContext
from app.agents.tools.common import region_id, rnd
from app.core.regions import get_region, scenario_key
from app.core.settings import get_settings


class RegionIn(BaseModel):
    region_id: str | None = Field(default=None, description="config/regions.yaml id")


async def list_sources(ctx: ToolContext, args: RegionIn) -> dict[str, Any]:
    sources = await ctx.get("/data-sources")
    out = []
    for s in sources:
        latest = s["datasets"][0] if s["datasets"] else None
        report = (latest or {}).get("quality_report") or {}
        out.append(
            {
                "id": s["id"],
                "name": s["name"],
                "license_class": s["license_class"],
                "freshness": s["freshness"],
                "refresh_cadence": s["refresh_cadence"],
                "latest_retrieved_at": latest["retrieved_at"] if latest else None,
                "rows": latest["row_count"] if latest else None,
                "synthetic": bool(report.get("synthetic")),
            }
        )
    return {
        "region_id": region_id(ctx, args.region_id),
        "sources": out,
        "summary": f"{len(out)} sources; "
        f"{sum(1 for s in out if s['freshness'] == 'never')} never loaded",
    }


async def freshness(ctx: ToolContext, args: RegionIn) -> dict[str, Any]:
    rid = region_id(ctx, args.region_id)
    region = get_region(rid)
    sources = await ctx.get("/data-sources")
    stale = [s["id"] for s in sources if s["freshness"] == "stale"]
    never = [s["id"] for s in sources if s["freshness"] == "never"]
    demand = await ctx.get("/demand/run")
    async with ctx.db() as session:
        cells = (
            await session.execute(
                text("select count(*) from region_cell where region_id = :r"), {"r": rid}
            )
        ).scalar_one()
        default = scenario_key(rid, "x") == "x"
        where = "scenario not like '%:%'" if default else "scenario like :p"
        scenarios = [
            s
            for (s,) in (
                await session.execute(
                    text(f"select distinct scenario from h3_cell_feature where {where}"),
                    {} if default else {"p": f"{rid}:%"},
                )
            ).all()
        ]
        stations = (
            await session.execute(
                text(
                    "select count(*) from charging_station where geom && "
                    "ST_MakeEnvelope(:x0, :y0, :x1, :y1, 4326)"
                ),
                {
                    "x0": region.bbox.min_lng,
                    "y0": region.bbox.min_lat,
                    "x1": region.bbox.max_lng,
                    "y1": region.bbox.max_lat,
                },
            )
        ).scalar_one()
    gaps = []
    if not cells:
        gaps.append("no population/H3 cells for this region (run make ingest-population)")
    if not any(s.endswith("base") for s in scenarios):
        gaps.append("no base demand scenario for this region (run make demand)")
    if demand["synthetic_registrations"]:
        gaps.append(
            "vehicle registrations are SYNTHETIC placeholders (no VAHAN export loaded): "
            "demand figures are illustrative, confidence NONE"
        )
    if demand["placeholders_in_use"]:
        gaps.append(f"{len(demand['placeholders_in_use'])} demand assumptions are placeholders")
    return {
        "region_id": rid,
        "region_name": region.name,
        "h3_cells": cells,
        "demand_scenarios": sorted(scenarios),
        "stations_in_region_bbox": stations,
        "sources_stale": stale,
        "sources_never_loaded": never,
        "demand_run": {
            "synthetic_registrations": demand["synthetic_registrations"],
            "observed_window": demand["observed_window"],
            "registration_sources": demand["registration_sources"],
            "placeholders_in_use": demand["placeholders_in_use"],
            "stations_with_assumed_count": demand["stations_with_assumed_count"],
            "charge_points_with_assumed_kw": demand["charge_points_with_assumed_kw"],
            "evidence_id": demand["evidence_id"],
        },
        "gaps": gaps,
        "summary": f"{len(gaps)} data gaps; {len(stale)} stale, {len(never)} never loaded",
    }


class DatasetQuery(BaseModel):
    query: str = Field(min_length=2, max_length=200)


async def search_dataset(ctx: ToolContext, args: DatasetQuery) -> dict[str, Any]:
    """data.gov.in catalogue search. Not wired to the live catalogue: without a key the
    registry's government sources are listed so the agent can say what is known."""
    registry = await ctx.get("/data-sources")
    known = [
        {"id": s["id"], "name": s["name"], "freshness": s["freshness"]}
        for s in registry
        if s["license_class"] == "GOVERNMENT"
    ]
    configured = bool(get_settings().data_gov_in_api_key)
    return {
        "configured": False,
        "note": (
            "Live data.gov.in catalogue search is not implemented in this build"
            + ("" if configured else " and DATA_GOV_IN_API_KEY is not set")
            + "; these government sources are registered locally."
        ),
        "query": args.query,
        "registered_government_sources": rnd(known),
    }


TOOLS = [
    Tool(
        "catalog.list_sources",
        "catalog",
        "List every registered data source with licence class, freshness (fresh/stale/"
        "never), latest snapshot time and row count.",
        RegionIn,
        list_sources,
    ),
    Tool(
        "catalog.freshness",
        "catalog",
        "Data readiness for a region: H3 cells, demand scenarios available, stations, "
        "stale/missing sources, whether registrations are synthetic, placeholders in use, "
        "and a list of gaps that limit confidence.",
        RegionIn,
        freshness,
    ),
    Tool(
        "gov.search_dataset",
        "gov",
        "Search the government open-data catalogue for a dataset (e.g. VAHAN registrations, "
        "charger lists). Reports what is registered locally when live search is unavailable.",
        DatasetQuery,
        search_dataset,
        returns_documents=True,
    ),
]

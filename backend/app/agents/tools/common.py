"""Helpers shared by the tool modules."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from pydantic import BaseModel, Field

from app.agents.tools.base import ToolContext, ToolError
from app.core.regions import get_region


def rnd(x: Any, digits: int = 3) -> Any:
    """Round floats to `digits` significant figures (keeps results short; the model and
    the validator both see the rounded value)."""
    if isinstance(x, float):
        if x == 0 or x != x:
            return x
        from math import floor, log10

        return round(x, max(0, digits - 1 - floor(log10(abs(x)))))
    if isinstance(x, dict):
        return {k: rnd(v, digits) for k, v in x.items()}
    if isinstance(x, list):
        return [rnd(v, digits) for v in x]
    return x


def region_id(ctx: ToolContext, given: str | None) -> str:
    try:
        return get_region(given or ctx.state.get("region_id")).id
    except KeyError as exc:
        raise ToolError(str(exc)) from exc


def run_id(ctx: ToolContext, given: str | None) -> str:
    rid = given or ctx.state.get("run_id")
    if not rid:
        raise ToolError("No planning run yet in this session; one is created after the plan")
    return str(rid)


async def base_optimisation_id(ctx: ToolContext, rid: str) -> str:
    run = await ctx.get(f"/runs/{rid}")
    base = (run["funnel"].get("optimisation") or {}).get("base_id")
    if not base:
        raise ToolError(
            "This run has no optimised plan yet (it stopped after scoring and awaits "
            "confirmation, or failed)"
        )
    ctx.state["optimisation_id"] = base
    return str(base)


async def wait_for_run(ctx: ToolContext, rid: str, agent: str | None) -> dict[str, Any]:
    """Poll a planning run until it finishes, relaying its progress as events."""
    deadline = time.monotonic() + ctx.run_wait_seconds
    seen = int(ctx.state.get("_progress_seen", {}).get(rid, 0))
    while True:
        run = await ctx.get(f"/runs/{rid}")
        for entry in run["progress"][seen:]:
            await ctx.emit("progress", agent, {"run_id": rid, **entry})
        seen = len(run["progress"])
        ctx.state.setdefault("_progress_seen", {})[rid] = seen
        if run["status"] in ("succeeded", "failed"):
            return dict(run)
        if time.monotonic() > deadline:
            raise ToolError(
                f"Run {rid} is still {run['status']} after {ctx.run_wait_seconds}s; "
                "check again with results.get_run"
            )
        await asyncio.sleep(2.0)


class Around(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    radius_m: int = Field(default=2000, ge=50, le=50000)


class Area(BaseModel):
    """Where to look: a bounding box, a circle, or the session's corridor."""

    bbox: list[float] | None = Field(
        default=None, min_length=4, max_length=4, description="[min_lng, min_lat, max_lng, max_lat]"
    )
    around: Around | None = None
    corridor: bool = Field(default=False, description="Use the session's route corridor")


async def area_wkt(ctx: ToolContext, area: Area) -> str:
    """The area as WKT (EPSG:4326), validated. Defaults to the region's bounding box."""
    from sqlalchemy import text

    if area.corridor:
        corridor = ctx.state.get("corridor")
        if not corridor:
            raise ToolError("No corridor in this session yet; call geo.corridor first")
        from app.services.candidate_run import corridor_area

        async with ctx.db() as session:
            return str((await corridor_area(session, corridor)).wkt)
    if area.around:
        a = area.around
        async with ctx.db() as session:
            return str(
                (
                    await session.execute(
                        text(
                            "select ST_AsText(ST_Buffer(ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)"
                            "::geography, :r)::geometry)"
                        ),
                        {"lng": a.lng, "lat": a.lat, "r": a.radius_m},
                    )
                ).scalar_one()
            )
    if area.bbox:
        x0, y0, x1, y1 = area.bbox
        if not (x0 < x1 and y0 < y1) or (x1 - x0) * (y1 - y0) > 4.0:
            raise ToolError("bbox must be [min_lng, min_lat, max_lng, max_lat], at most ~2x2 deg")
        return f"POLYGON(({x0} {y0},{x1} {y0},{x1} {y1},{x0} {y1},{x0} {y0}))"
    b = get_region(ctx.state.get("region_id")).bbox
    return (
        f"POLYGON(({b.min_lng} {b.min_lat},{b.max_lng} {b.min_lat},{b.max_lng} {b.max_lat},"
        f"{b.min_lng} {b.max_lat},{b.min_lng} {b.min_lat}))"
    )

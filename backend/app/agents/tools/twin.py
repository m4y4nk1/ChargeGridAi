"""twin.* (Demand and Grid & Energy agents): region-wide what-ifs on the demand grid."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.agents.tools.base import Tool, ToolContext


class TwinToolIn(BaseModel):
    kind: Literal["add_chargers", "adoption", "grid_constraints"]
    n: int = Field(default=10_000, ge=1, le=1_000_000, description="chargers to add")
    charger_class: str = Field(default="DC_60", description="e.g. DC_60, DC_120, AC_22")
    multiplier: float = Field(default=1.3, ge=0.1, le=5, description="adoption multiplier")
    year: int = Field(default=2030, ge=2026, le=2035)
    regions: list[str] | None = Field(default=None, description="default: all onboarded")


async def twin_run(ctx: ToolContext, args: TwinToolIn) -> dict[str, Any]:
    run = await ctx.post("/twin/runs", args.model_dump(exclude_none=True))
    r = run["result"]
    out: dict[str, Any] = {
        "twin_run_id": run["id"],
        "kind": r["kind"],
        "regions": r["regions"],
        "demand_confidence": r["demand_confidence"],
        "caveats": r["caveats"],
        "scope_note": r["scope_note"],
    }
    if "placement" in r:
        p = r["placement"]
        out["placement"] = {
            k: p[k]
            for k in (
                "n_requested",
                "charger_class",
                "placed",
                "unplaced",
                "demand_limited",
                "cells_used",
                "gap_before_kwh",
                "gap_after_kwh",
                "served_added_kwh",
                "gap_closed_share",
                "kwh_per_charger_per_day",
                "by_region",
            )
        }
        out["placement"]["top_cells"] = p.get("top_cells", [])[:8]
    if "grid" in r:
        g = r["grid"]
        out["grid"] = {
            "mode": g["mode"],
            "confidence": g["confidence"],
            "note": g["note"],
            "substations_total": g["substations_total"],
            "top_substations": g["substations"][:8],
            "constrained": g["constrained"],
        }
    if "adoption" in r:
        out["adoption"] = r["adoption"]
    out["summary"] = f"{r['kind']} over {', '.join(r['regions'])}"
    return out


TOOLS = [
    Tool(
        "twin.run",
        "twin",
        "Region-wide what-if on the demand grid: add_chargers (where N chargers of a class "
        "would serve the most unserved demand), adoption (demand, gap and charge points "
        "needed under an adoption multiplier), grid_constraints (connected load per nearest "
        "substation after adding N chargers; DISCOM headroom where available). Screening "
        "estimates, not site plans.",
        TwinToolIn,
        twin_run,
    ),
]

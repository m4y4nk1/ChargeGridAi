"""demand.*, gap.*, scenario.*, candidates/feasibility/score, optimizer.*, whatif.*."""

from __future__ import annotations

from collections import Counter
from typing import Any, Literal

import h3
from pydantic import BaseModel, Field
from shapely import wkt as shapely_wkt
from shapely.geometry import Point
from sqlalchemy import text

from app.agents.tools.base import Tool, ToolContext, ToolError
from app.agents.tools.common import region_id, rnd, run_id, wait_for_run
from app.core.regions import get_region
from app.services.demand_run import scenario_name

Case = Literal["slow", "base", "fast"]


class DemandIn(BaseModel):
    year: int = Field(ge=2026, le=2035)
    case: Case = "base"
    adoption_multiplier: float = Field(default=1.0, gt=0, le=5)
    area_name: str | None = Field(
        default=None, description="District or taluka name; default: the region's default area"
    )
    region_id: str | None = None


def _scenario(args: DemandIn) -> str:
    """The API takes the un-namespaced key ("base", "fastx1.3") plus the region."""
    return scenario_name(args.case, args.adoption_multiplier, None)


async def _area_id(ctx: ToolContext, rid: str, name: str | None) -> int:
    region = get_region(rid)
    if not name:
        if region.default_area is None:
            raise ToolError(f"Region {rid} has no default area; give area_name")
        return region.default_area
    async with ctx.db() as session:
        row = (
            await session.execute(
                text(
                    "select osm_id from admin_boundary where name ilike :n "
                    "order by (lower(name) = lower(:exact)) desc, admin_level limit 1"
                ),
                {"n": f"%{name}%", "exact": name},
            )
        ).first()
    if row is None:
        raise ToolError(f"No district/taluka named like '{name}'")
    return int(row[0])


async def _layer_totals(
    ctx: ToolContext, path: str, rid: str, args: DemandIn, top_n: int = 0
) -> dict[str, Any]:
    """Sum an H3 layer over the region, or over the session corridor when one is set."""
    layer = await ctx.get(path, year=args.year, scenario=_scenario(args), region=rid)
    cells = list(zip(layer["h3"], layer["value"], strict=True))
    scope = "region"
    corridor = ctx.state.get("corridor")
    if corridor:
        from app.services.candidate_run import corridor_area

        async with ctx.db() as session:
            poly = shapely_wkt.loads((await corridor_area(session, corridor)).wkt)
        cells = [(c, v) for c, v in cells if poly.contains(Point(h3.cell_to_latlng(c)[::-1]))]
        scope = f"corridor ({corridor.get('label')}, {corridor['buffer_m']} m buffer)"
    positive = sorted(((v, c) for c, v in cells if v and v > 0), reverse=True)
    out: dict[str, Any] = {
        "scope": scope,
        "unit": layer["unit"],
        "cells": len(cells),
        "total": round(sum(v for _, v in cells if v)),
        "cells_positive": len(positive),
        "confidence": layer["confidence"],
    }
    if top_n:
        out["top_cells"] = [
            {
                "h3": c,
                "value": round(v),
                "lat": round(h3.cell_to_latlng(c)[0], 5),
                "lng": round(h3.cell_to_latlng(c)[1], 5),
            }
            for v, c in positive[:top_n]
        ]
    return out


async def forecast(ctx: ToolContext, args: DemandIn) -> dict[str, Any]:
    rid = region_id(ctx, args.region_id)
    area = await _area_id(ctx, rid, args.area_name)
    k = await ctx.get(f"/regions/{area}/kpis", year=args.year, scenario=_scenario(args), region=rid)
    public = await _layer_totals(ctx, "/demand/h3", rid, args)
    return {
        "region_id": rid,
        "scenario": _scenario(args),
        "year": args.year,
        "area": {"id": area, "name": k["area"].get("name")},
        "area_kpis": {
            "population": k["population"],
            "current_evs": k["current_evs"],
            "current_evs_as_of": k["current_evs_as_of"],
            "projected_evs": k["projected_evs"],
            "projected_evs_by_segment": k["projected_evs_by_segment"],
            "public_kwh_per_day": k["public_kwh_per_day"],
            "sessions_per_day": k["sessions_per_day"],
            "existing": k["existing"],
            "evs_per_public_charge_point": k["evs_per_public_charge_point"],
        },
        "public_kwh_per_day_in_scope": public,
        "confidence": k["confidence"],
        "synthetic_registrations": k["run"]["synthetic_registrations"],
        "placeholders_in_use": k["run"]["placeholders_in_use"],
        "evidence_ids": k["evidence_ids"],
        "summary": f"{k['area'].get('name')} {args.year}: projected EVs P50 "
        f"{k['projected_evs']['p50']}, public demand P50 {k['public_kwh_per_day']['p50']} kWh/day",
    }


class GapIn(DemandIn):
    top_n: int = Field(default=10, ge=0, le=25)


async def gap(ctx: ToolContext, args: GapIn) -> dict[str, Any]:
    rid = region_id(ctx, args.region_id)
    area = await _area_id(ctx, rid, args.area_name)
    k = await ctx.get(f"/regions/{area}/kpis", year=args.year, scenario=_scenario(args), region=rid)
    layer = await _layer_totals(ctx, "/gap/h3", rid, args, args.top_n)
    return {
        "region_id": rid,
        "scenario": _scenario(args),
        "year": args.year,
        "area": {
            "name": k["area"].get("name"),
            "gap_kwh_per_day": k["gap_kwh_per_day"],
            "additional_charge_points": k["additional_charge_points"],
        },
        "gap_in_scope": layer,
        "confidence": k["confidence"]["gap"],
        "evidence_ids": k["evidence_ids"][:1],
        "summary": f"unserved demand in scope {layer['total']} kWh/day over "
        f"{layer['cells_positive']} cells",
    }


class ScenarioCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    target_year: int = Field(ge=2026, le=2035)
    adoption_case: Case = "base"
    adoption_multiplier: float = Field(default=1.0, gt=0, le=5)
    charger_classes: list[str] = Field(min_length=1)
    budget_inr: int | None = Field(default=None, ge=0)
    max_sites: int | None = Field(default=None, ge=1, le=500)
    weight_profile: str | None = None
    region_id: str | None = None
    use_corridor: bool = Field(default=True, description="Restrict to the session corridor")


async def create_scenario(ctx: ToolContext, args: ScenarioCreateIn) -> dict[str, Any]:
    body = args.model_dump(exclude={"use_corridor"})
    body["region_id"] = region_id(ctx, args.region_id)
    if args.use_corridor and ctx.state.get("corridor"):
        body["corridor"] = ctx.state["corridor"]
    scenario = await ctx.post("/scenarios", body)
    ctx.state["scenario_id"] = scenario["id"]
    ctx.state["region_id"] = scenario["region_id"]
    ctx.state.pop("run_id", None)
    return {
        "scenario_id": scenario["id"],
        **{
            k: scenario[k]
            for k in (
                "name",
                "region_id",
                "target_year",
                "adoption_case",
                "adoption_multiplier",
                "charger_classes",
                "budget_inr",
                "max_sites",
                "weight_profile",
            )
        },
        "corridor": (body.get("corridor") or {}).get("label"),
        "summary": f"scenario '{scenario['name']}' in {scenario['region_id']}"
        + (f" along {body['corridor']['label']}" if body.get("corridor") else ""),
    }


def _funnel(run: dict[str, Any]) -> dict[str, Any]:
    f = run["funnel"]
    return {
        "theoretical": (f.get("theoretical") or {}).get("total"),
        "candidates": (f.get("candidates") or {}).get("total"),
        "candidates_by_origin": (f.get("candidates") or {}).get("by_origin"),
        "feasible": (f.get("feasible") or {}).get("total"),
        "rejected": (f.get("rejected") or {}).get("total"),
        "rejected_by_reason": (f.get("rejected") or {}).get("by_reason"),
        "scored": (f.get("scored") or {}).get("total"),
        "shortlisted": f.get("shortlisted"),
        "selected": f.get("selected"),
    }


class ScenarioRef(BaseModel):
    scenario_id: str | None = Field(default=None, description="Default: this session's scenario")


async def generate(ctx: ToolContext, args: ScenarioRef) -> dict[str, Any]:
    """Start the planning run up to scoring (candidates, feasibility, catchments, scores)
    and wait for it. The optimisation waits for the user's confirmation."""
    sid = args.scenario_id or ctx.state.get("scenario_id")
    if not sid:
        raise ToolError("No scenario yet; call scenario.create first")
    rid = ctx.state.get("run_id")
    if not rid or ctx.state.get("run_scenario_id") != sid:
        run = await ctx.post("/runs", {"scenario_id": sid, "stop_after": "scoring"})
        rid = run["id"]
        ctx.state.update(run_id=rid, run_scenario_id=sid)
        await ctx.emit("progress", None, {"step": "run", "status": "started", "run_id": rid})
    run = await wait_for_run(ctx, rid, None)
    if run["status"] == "failed":
        raise ToolError(f"Planning run failed: {run['error']}")
    return {
        "run_id": rid,
        "status": run["status"],
        "funnel": _funnel(run),
        "demand_synthetic": run["demand_synthetic"],
        "placeholders_in_use": len(run["placeholders_in_use"]),
        "awaiting_confirmation": bool(
            (run["funnel"].get("optimisation") or {}).get("awaiting_confirmation")
        ),
        "summary": "funnel "
        + " -> ".join(f"{k} {v}" for k, v in _funnel(run).items() if isinstance(v, int)),
    }


class RunRef(BaseModel):
    run_id: str | None = Field(default=None, description="Default: this session's run")


async def feasibility(ctx: ToolContext, args: RunRef) -> dict[str, Any]:
    rid = run_id(ctx, args.run_id)
    run = await ctx.get(f"/runs/{rid}")
    rejected = await ctx.get(f"/runs/{rid}/rejected")
    reasons = Counter(code for r in rejected for code in r.get("reason_codes", []))
    return {
        "run_id": rid,
        "feasible": (run["funnel"].get("feasible") or {}).get("total"),
        "rejected": len(rejected),
        "rejected_by_rule": dict(reasons),
        "rules": {
            "F01": "Restricted land (water, forest, military, protected, airport)",
            "F02": "Too far from a drivable road",
            "F03": "Existing DC station already serves this area",
            "F04": "Too far from grid supply",
            "F05": "Residential interior street, no arterial access (DC)",
            "F06": "Hazard overlay (flood zones)",
            "F07": "Duplicate of a selected site",
        },
        "examples": [
            {
                "name": r.get("name"),
                "host_type": r.get("host_type"),
                "reason_codes": r.get("reason_codes"),
            }
            for r in rejected[:8]
        ],
    }


class ScoreIn(RunRef):
    weight_profile: str | None = Field(
        default=None, description="city_equity | cpo_commercial | highway | oem_coverage"
    )
    limit: int = Field(default=10, ge=1, le=30)


async def score(ctx: ToolContext, args: ScoreIn) -> dict[str, Any]:
    rid = run_id(ctx, args.run_id)
    r = await ctx.get(f"/runs/{rid}/ranking", profile=args.weight_profile, limit=args.limit)
    return {
        "run_id": rid,
        "weight_profile": r["weight_profile"],
        "weights": r["weights"],
        "scored": r["scored"],
        "confidence": r["confidence"],
        "uninformative_sub_scores": r["uninformative"],
        "note": r["note"],
        "top": [
            {
                "site_id": s["site_id"],
                "name": s["name"],
                "host_type": s["host_type"],
                "rank": s["rank"],
                "score_total": s["score_total"],
                "robustness": rnd(s["robustness"]),
                "sub_scores": rnd(s["sub_scores"]),
            }
            for s in r["sites"]
        ],
    }


async def _plan_summary(ctx: ToolContext, rid: str) -> dict[str, Any]:
    run = await ctx.get(f"/runs/{rid}")
    opt = run["funnel"].get("optimisation") or {}
    base_id = opt.get("base_id")
    if not base_id:
        return {"run_id": rid, "status": run["status"], "optimisation": opt}
    ctx.state["optimisation_id"] = base_id
    plan = await ctx.get(f"/optimisations/{base_id}")
    return {
        "run_id": rid,
        "optimisation_id": base_id,
        "status": plan["status"],
        "kpis": rnd(plan["kpis"]),
        "solver": rnd(
            {k: plan["solver_stats"].get(k) for k in ("solver", "status", "gap", "solve_s")}
        ),
        "label": plan["label_note"],
        "summary": f"{len(plan['sites'])} sites, cost {plan['kpis'].get('cost_inr')} INR, "
        f"solver {plan['status']}",
        "sites": [
            {
                "site_id": s["site_id"],
                "name": s["name"],
                "host_type": s["host_type"],
                "bundle": s["bundle"],
                "charge_points": s["charge_points"],
                "cost_inr": s["cost_inr"],
                "served_kwh_per_day": rnd(s["served_kwh"]),
                "utilisation": s["utilisation"],
                "lat": round(s["lat"], 5),
                "lng": round(s["lng"], 5),
            }
            for s in plan["sites"]
        ],
    }


async def optimise(ctx: ToolContext, args: RunRef) -> dict[str, Any]:
    """MCLP network optimisation (+ why-not, strategies, sizing and finance of the plan).
    Only runs after the user confirmed at the checkpoint."""
    rid = run_id(ctx, args.run_id)
    run = await ctx.get(f"/runs/{rid}")
    if (run["funnel"].get("optimisation") or {}).get("awaiting_confirmation"):
        if not ctx.state.get("confirmed"):
            raise ToolError("The user hasn't confirmed the optimisation yet")
        await ctx.post(f"/runs/{rid}/optimise", {})
        await ctx.emit("progress", None, {"step": "optimise", "status": "started", "run_id": rid})
    run = await wait_for_run(ctx, rid, None)
    if run["status"] == "failed":
        raise ToolError(f"Optimisation failed: {run['error']}")
    return await _plan_summary(ctx, rid)


class WhatIfIn(RunRef):
    budget_inr: int | None = Field(default=None, gt=0)
    max_sites: int | None = Field(default=None, ge=1, le=500)
    adoption_multiplier: float | None = Field(default=None, gt=0, le=5)


async def whatif(ctx: ToolContext, args: WhatIfIn) -> dict[str, Any]:
    rid = run_id(ctx, args.run_id)
    body = args.model_dump(exclude={"run_id"}, exclude_none=True)
    if not body:
        raise ToolError("Give at least one of budget_inr, max_sites, adoption_multiplier")
    o = await ctx.post(f"/runs/{rid}/whatif", body)
    return {
        "optimisation_id": o["id"],
        "overrides": body,
        "kpis": rnd(o["kpis"]),
        "diff": rnd(o["diff"]),
        "sites": [
            {"name": s["name"], "bundle": s["bundle"], "cost_inr": s["cost_inr"]}
            for s in o["sites"]
        ],
    }


TOOLS = [
    Tool(
        "demand.forecast",
        "demand",
        "EV stock and public charging demand for a target year and adoption case: KPIs for "
        "a district/taluka (default: the region's default area) plus total public kWh/day "
        "over the region or the session corridor. P10/P50/P90 bands with confidence.",
        DemandIn,
        forecast,
    ),
    Tool(
        "gap.compute",
        "gap",
        "Unserved public charging demand (kWh/day) after existing supply, for an area and "
        "summed over the region or session corridor, with the top gap H3 cells.",
        GapIn,
        gap,
    ),
    Tool(
        "scenario.create",
        "scenario",
        "Create a planning scenario (target year, adoption case, charger classes, budget, "
        "max sites, weight profile), restricted to the session corridor when one exists.",
        ScenarioCreateIn,
        create_scenario,
    ),
    Tool(
        "candidates.generate",
        "run",
        "Run candidate generation, feasibility screening, catchments and scoring for the "
        "scenario (stops before optimisation) and return the funnel. Waits for the run.",
        ScenarioRef,
        generate,
    ),
    Tool(
        "feasibility.run",
        "run",
        "Feasibility results of the run: feasible/rejected counts, rejections by rule code, "
        "examples.",
        RunRef,
        feasibility,
    ),
    Tool(
        "score.run",
        "run",
        "Ranked candidates with sub-scores (percentiles) under the run's weight profile or "
        "another one; flags uninformative sub-scores and rank robustness.",
        ScoreIn,
        score,
    ),
    Tool(
        "optimizer.optimize_network",
        "optimizer",
        "Optimise the charging network (MCLP within budget and site limit), then why-not, "
        "strategies, sizing and finance. Requires the user's confirmation.",
        RunRef,
        optimise,
    ),
    Tool(
        "whatif.apply",
        "whatif",
        "Re-optimise the run's plan with a different budget, site limit or adoption "
        "multiplier (under 10 s) and return KPIs and the diff against the base plan.",
        WhatIfIn,
        whatif,
    ),
]

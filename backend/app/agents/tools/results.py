"""results.*, evidence.*, policy.* (Report agent; results are read-only stored outputs)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.agents.tools.base import Tool, ToolContext, ToolError
from app.agents.tools.common import rnd, run_id
from app.agents.tools.planning import RunRef, _funnel, _plan_summary


async def get_run(ctx: ToolContext, args: RunRef) -> dict[str, Any]:
    rid = run_id(ctx, args.run_id)
    run = await ctx.get(f"/runs/{rid}")
    out: dict[str, Any] = {
        "run_id": rid,
        "status": run["status"],
        "scenario": {
            k: run["scenario"][k]
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
        "funnel": _funnel(run),
        "demand_synthetic": run["demand_synthetic"],
        "placeholders_in_use": run["placeholders_in_use"],
        "scoring": rnd(
            {
                k: (run["scoring"] or {}).get(k)
                for k in ("weight_profile", "confidence", "uninformative", "catchment_minutes")
            }
        ),
        "error": run["error"],
    }
    opt = run["funnel"].get("optimisation") or {}
    if opt.get("base_id"):
        out["plan"] = await _plan_summary(ctx, rid)
        out["economics"] = rnd(opt.get("economics"))
        strategies = await ctx.get(f"/runs/{rid}/optimisations", kind="strategy")
        out["strategies"] = [
            {
                "strategy": s["strategy"],
                "label": s["label"],
                "on_front": s["on_front"],
                "kpis": rnd(
                    {
                        k: s["kpis"].get(k)
                        for k in (
                            "n_sites",
                            "cost_inr",
                            "served_kwh",
                            "coverage_share",
                            "served_population",
                        )
                    }
                ),
            }
            for s in strategies
        ]
    else:
        out["plan"] = {"awaiting_confirmation": bool(opt.get("awaiting_confirmation"))}
    return out


class SiteRef(BaseModel):
    site_id: str
    run_id: str | None = None


async def get_site(ctx: ToolContext, args: SiteRef) -> dict[str, Any]:
    rid = run_id(ctx, args.run_id)
    d = await ctx.get(f"/runs/{rid}/sites/{args.site_id}")
    sc = d.get("scoring") or {}
    return {
        "site_id": d["id"],
        "label": d["label"],
        "name": d["name"],
        "host_type": d["host_type"],
        "origin": d["origin"],
        "lat": round(d["lat"], 5),
        "lng": round(d["lng"], 5),
        "status": d["status"],
        "feasibility": {
            "passed": (d.get("feasibility") or {}).get("passed"),
            "reason_codes": (d.get("feasibility") or {}).get("reason_codes"),
        },
        "features": rnd(d.get("features")),
        "scoring": {
            "rank": sc.get("rank"),
            "score_total": sc.get("score_total"),
            "sub_scores": rnd(sc.get("sub_scores")),
            "robustness": rnd(sc.get("robustness")),
            "catchment": rnd(sc.get("catchment")),
        },
        "plan": rnd(d.get("plan")),
        "evidence_ids": d["evidence_ids"],
    }


class WhyNotIn(RunRef):
    site_id: str | None = Field(default=None, description="Default: every explained site")


async def why_not(ctx: ToolContext, args: WhyNotIn) -> dict[str, Any]:
    rid = run_id(ctx, args.run_id)
    rows = await ctx.get(f"/runs/{rid}/why-not")
    if args.site_id:
        rows = [r for r in rows if r["site_id"] == args.site_id]
        if not rows:
            raise ToolError("No why-not explanation for that site (not a strong excluded one)")
    return {
        "run_id": rid,
        "sites": [
            {
                "site_id": r["site_id"],
                "name": r["name"],
                "host_type": r["host_type"],
                "rank": r["rank"],
                "score_total": r["score_total"],
                "status": r["why_not"].get("status"),
                "cost_delta_inr": r["why_not"].get("cost_delta_inr"),
                "served_delta_kwh": rnd(r["why_not"].get("served_delta_kwh")),
                "objective_delta": rnd(r["why_not"].get("objective_delta")),
                "displaced": [x.get("name") for x in r["why_not"].get("displaced", [])],
                "spacing_conflicts": [
                    x.get("name") for x in r["why_not"].get("spacing_conflicts", [])
                ],
                "deciding_sub_score": r["why_not"].get("deciding_sub_score"),
                "deciding_points": r["why_not"].get("deciding_points"),
            }
            for r in rows[:15]
        ],
    }


class EvidenceIn(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=20)


async def evidence(ctx: ToolContext, args: EvidenceIn) -> dict[str, Any]:
    rows = await ctx.get("/evidence", ids=",".join(args.ids))
    return {
        "evidence": [
            {
                "id": e["id"],
                "metric": e["metric"],
                "subject": f"{e['subject_type']}:{e['subject_id']}",
                "value": rnd(e["value_num"]) if e["value_num"] is not None else e["value_text"],
                "unit": e["unit"],
                "p10": rnd(e["p10"]),
                "p90": rnd(e["p90"]),
                "method": e["method"],
                "confidence": e["confidence"],
                "sources": sorted(
                    {
                        f"{s['source_name']} ({s['license_class']}, retrieved "
                        f"{s['retrieved_at'][:10]})"
                        for s in e["sources"]
                    }
                ),
            }
            for e in rows
        ]
    }


class PolicyIn(BaseModel):
    query: str = Field(min_length=3, max_length=300)
    top_k: int = Field(default=5, ge=1, le=8)


async def policy(ctx: ToolContext, args: PolicyIn) -> dict[str, Any]:
    from app.agents.rag.policy import search

    async with ctx.db() as session:
        hits = await search(session, args.query, args.top_k)
    if not hits:
        return {
            "query": args.query,
            "chunks": [],
            "note": "Policy corpus is empty; run make ingest-policy",
        }
    return {"query": args.query, "chunks": hits}


TOOLS = [
    Tool(
        "results.get_run",
        "results",
        "Stored results of a planning run: scenario, funnel, the optimised plan (sites, "
        "KPIs), portfolio economics, alternative strategies, data caveats.",
        RunRef,
        get_run,
    ),
    Tool(
        "results.get_site",
        "results",
        "Stored detail of one candidate site: feasibility, features, scores and sub-scores, "
        "catchment indices, plan role, evidence ids.",
        SiteRef,
        get_site,
    ),
    Tool(
        "results.why_not",
        "results",
        "Why strong candidates were left out of the plan: forced-in re-solve cost, sites "
        "they would displace, spacing conflicts and the deciding sub-score.",
        WhyNotIn,
        why_not,
    ),
    Tool(
        "evidence.get",
        "evidence",
        "Evidence records by id: metric, value, bands, method, confidence and the data "
        "sources (licence, retrieval date) behind them.",
        EvidenceIn,
        evidence,
    ),
    Tool(
        "policy.search",
        "policy",
        "Search Indian EV charging policy documents (Ministry of Power guidelines, PM "
        "E-DRIVE) and return passages with document and page for citation.",
        PolicyIn,
        policy,
        returns_documents=True,
    ),
]

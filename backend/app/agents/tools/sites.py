"""sizing.*, energy.*, grid.*, finance.* for sites of the session's optimised plan."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.agents.tools.base import Tool, ToolContext, ToolError
from app.agents.tools.common import base_optimisation_id, rnd, run_id

MAX_SITES = 25


class SitesIn(BaseModel):
    site_ids: list[str] = Field(
        default_factory=list,
        max_length=MAX_SITES,
        description="Sites of the plan; empty = every site in the plan",
    )
    run_id: str | None = None


async def _plan_sites(ctx: ToolContext, args: SitesIn) -> tuple[str, list[dict[str, Any]]]:
    rid = run_id(ctx, args.run_id)
    opt = await base_optimisation_id(ctx, rid)
    plan = await ctx.get(f"/optimisations/{opt}")
    sites = plan["sites"]
    if args.site_ids:
        wanted = set(args.site_ids)
        sites = [s for s in sites if s["site_id"] in wanted]
        missing = wanted - {s["site_id"] for s in sites}
        if missing:
            raise ToolError(f"Not in the plan: {sorted(missing)}")
    return opt, sites[:MAX_SITES]


async def _economics(ctx: ToolContext, opt: str, site_id: str, subsidy: bool = False) -> Any:
    try:
        return await ctx.get(
            f"/optimisations/{opt}/sites/{site_id}/economics", subsidy=str(subsidy).lower()
        )
    except ToolError as exc:
        return {"error": str(exc)}


async def size_sites(ctx: ToolContext, args: SitesIn) -> dict[str, Any]:
    opt, sites = await _plan_sites(ctx, args)
    out = []
    for s in sites:
        e = await _economics(ctx, opt, s["site_id"])
        if "error" in e:
            out.append({"site_id": s["site_id"], "name": s["name"], "skipped": e["error"]})
            continue
        z = e["sizing"]
        out.append(
            {
                "site_id": s["site_id"],
                "name": s["name"],
                "plan_bundle": z["plan_bundle"],
                "charger_class": z["charger_class"],
                "chargers": z["chargers"],
                "total_kw": z["total_kw"],
                "sessions_per_day": z["sessions_per_day"],
                "served_kwh_per_day": z["served_kwh_per_day"],
                "peak_hour": z["peak_hour"],
                "connectors_guns": z["connectors"]["guns"],
                "erlang": rnd(z["erlang"]),
                "simulation": rnd(
                    {
                        k: z["simulation"][k]
                        for k in (
                            "days",
                            "utilisation",
                            "p95_wait_min",
                            "mean_wait_min",
                            "prob_wait_over",
                        )
                    }
                ),
                "targets": z["targets"],
                "flags": z["flags"],
                "connection": z["electrical"]["connection"],
                "sanctioned_kw": z["electrical"]["sanctioned_kw"],
            }
        )
    sized = [o for o in out if "chargers" in o]
    return {
        "optimisation_id": opt,
        "method": "Erlang-C sizing checked with a 1000-day SimPy simulation",
        "sites": out,
        "totals": {
            "sites_sized": len(sized),
            "chargers": sum(o["chargers"] for o in sized),
            "total_kw": sum(o["total_kw"] for o in sized),
            "plan_charge_points": sum(o["plan_bundle"]["charge_points"] for o in sized),
        },
        "summary": f"{len(sized)} sites sized, {sum(o['chargers'] for o in sized)} chargers",
    }


async def energy(ctx: ToolContext, args: SitesIn) -> dict[str, Any]:
    opt, sites = await _plan_sites(ctx, args)
    out = []
    for s in sites[:10]:  # the dispatch LP takes a few seconds per site
        try:
            e = await ctx.get(f"/optimisations/{opt}/sites/{s['site_id']}/energy")
        except ToolError as exc:
            out.append({"site_id": s["site_id"], "name": s["name"], "skipped": str(exc)})
            continue
        o = e["open"]
        out.append(
            {
                "site_id": s["site_id"],
                "name": s["name"],
                "design_peak_kw": e["load"]["design_peak_kw"],
                "annual_kwh": e["load"]["annual_kwh"],
                "recommended": o["recommended"],
                "pv_limit_kwp": o["pv_limit"]["kwp"],
                "pv_limit_source": o["pv_limit"]["source"],
                "effect": rnd(o["effect"]),
                "scenarios": {
                    k: rnd(
                        {
                            "label": v["label"],
                            "capex": v["capex"],
                            "annual_total": v["annual"]["total"],
                            "pv_kwp": v.get("pv_kwp"),
                            "battery_kwh": v.get("battery_kwh"),
                            "contract_kw": v.get("contract_kw"),
                            "battery_kw": v.get("battery_kw"),
                            "connection": v.get("connection"),
                            "solar_share": v.get("solar_share"),
                            "status": v["status"],
                        }
                    )
                    for k, v in o["scenarios"].items()
                },
                "solar_source": o["solar_source"],
                "specific_yield_kwh_per_kwp": e["solar"]["specific_yield_kwh_per_kwp"],
                "google_solar_available": e["google_available"],
            }
        )
    return {
        "optimisation_id": opt,
        "method": "hourly dispatch LP over 12 representative days (NASA POWER irradiance)",
        "tariffs_note": "Tariffs and component costs are ILLUSTRATIVE placeholders",
        "sites": out,
        "summary": f"{len(out)} sites; recommended: "
        + ", ".join(sorted({str(o.get("recommended")) for o in out if "recommended" in o})),
        "note": "At most 10 sites per call" if len(sites) > 10 else None,
    }


async def grid_connection(ctx: ToolContext, args: SitesIn) -> dict[str, Any]:
    """Grid proxy per site: distance to the nearest mapped substation, confidence, and the
    connection the sized load needs. There is no DISCOM headroom data."""
    rid = run_id(ctx, args.run_id)
    opt, sites = await _plan_sites(ctx, args)
    out = []
    for s in sites:
        d = await ctx.get(f"/runs/{rid}/sites/{s['site_id']}")
        f = d.get("features") or {}
        e = await _economics(ctx, opt, s["site_id"])
        el = (e.get("sizing") or {}).get("electrical") or {}
        out.append(
            {
                "site_id": s["site_id"],
                "name": s["name"],
                "nearest_substation_m": f.get("nearest_substation_m"),
                "grid_confidence": f.get("grid_confidence"),
                "connection": el.get("connection"),
                "sanctioned_kw": el.get("sanctioned_kw"),
                "transformer_required": el.get("transformer_required"),
                "connection_cost_inr": el.get("connection_cost_inr"),
                "lt_max_sanctioned_kw": el.get("lt_max_sanctioned_kw"),
            }
        )
    return {
        "optimisation_id": opt,
        "sites": out,
        "summary": f"{len(out)} sites: "
        f"{sum(1 for o in out if o['connection'] == 'HT')} need HT, "
        f"{sum(1 for o in out if o['connection'] == 'LT')} LT",
        "note": "Grid headroom is a proxy (OSM substations/lines, confidence LOW); a DISCOM "
        "feasibility check is required before committing.",
    }


class FinanceIn(SitesIn):
    subsidy: bool = Field(
        default=False, description="Apply the configured scheme share to upstream grid costs"
    )


async def finance(ctx: ToolContext, args: FinanceIn) -> dict[str, Any]:
    opt, sites = await _plan_sites(ctx, args)
    out = []
    for s in sites:
        e = await _economics(ctx, opt, s["site_id"], args.subsidy)
        if "error" in e:
            out.append({"site_id": s["site_id"], "name": s["name"], "skipped": e["error"]})
            continue
        f = e["finance"]
        mc = f["monte_carlo"]
        out.append(
            {
                "site_id": s["site_id"],
                "name": s["name"],
                "capex_inr": f["capex_total"],
                "connection": f["connection"],
                "npv_base_inr": round(f["base"]["npv"]),
                "irr_base": f["base"]["irr"],
                "payback_years_base": f["base"]["payback_years"],
                "npv_inr": rnd({k: mc["npv"][k] for k in ("p10", "p50", "p90")}),
                "irr": rnd({k: mc["irr"][k] for k in ("p10", "p50", "p90")}),
                "cases_npv_inr": {k: round(v["npv"]) for k, v in f["cases"].items()},
                "top_sensitivities": [
                    {"input": t["label"], "npv_swing_inr": round(t["swing"])}
                    for t in f["tornado"][:3]
                ],
                "selling_price_inr_per_kwh": f["selling_price_inr_per_kwh"],
                "grid_energy_inr_per_kwh": rnd(f["grid_energy_inr_per_kwh"]),
            }
        )
    rid = run_id(ctx, args.run_id)
    run = await ctx.get(f"/runs/{rid}")
    budget = run["scenario"]["budget_inr"]
    valued = [o for o in out if "capex_inr" in o]
    capex = sum(o["capex_inr"] for o in valued)
    plan = await ctx.get(f"/optimisations/{opt}")
    return {
        "optimisation_id": opt,
        "method": "10-year cash flow, 12% discount rate, 5000-draw Monte Carlo",
        "subsidy_applied": args.subsidy,
        "sites": out,
        "portfolio": {
            "sites_valued": len(valued),
            "capex_sized_inr": capex,
            "plan_cost_inr": plan["kpis"].get("cost_inr"),
            "budget_inr": budget,
            "sized_capex_within_budget": (capex <= budget) if budget else None,
            "npv_base_sum_inr": sum(o["npv_base_inr"] for o in valued),
            "npv_p50_sum_inr": round(sum(o["npv_inr"]["p50"] or 0 for o in valued)),
            "sites_npv_p50_positive": sum(1 for o in valued if (o["npv_inr"]["p50"] or 0) > 0),
            "note": "A sum of per-site P50s is not a portfolio P50.",
        },
        "summary": f"{len(valued)} sites valued, capex {capex} INR, "
        f"{sum(1 for o in valued if (o['npv_inr']['p50'] or 0) > 0)} with positive P50 NPV",
        "placeholders_note": "Costs, tariffs and prices are ILLUSTRATIVE placeholders "
        "(config/costs, config/tariffs); confidence NONE until replaced.",
    }


class RunOnly(BaseModel):
    run_id: str | None = None


async def grid_impact(ctx: ToolContext, args: RunOnly) -> dict[str, Any]:
    """Module E summary: plan load curve, charger profile, and grid assets (DISCOM-backed
    utilisation and upgrades, or the proximity-only estimate)."""
    rid = run_id(ctx, args.run_id)
    g = await ctx.get("/grid/impact", run_id=rid)
    lc = g["load_curve"]
    return {
        "mode": g["mode"],
        "badge": g["badge"],
        "confidence": g["confidence"],
        "charger_profile": g["charger_profile"],
        "load_curve": {
            "peak_kw": lc["peak_kw"],
            "peak_hour": lc["peak_hour"],
            "daily_kwh": lc["daily_kwh"],
            "sanctioned_kw_total": lc["sanctioned_kw_total"],
            "segment_peak_kw": {k: max(v) for k, v in lc["by_segment"].items()},
        },
        "assets": [
            {
                k: a[k]
                for k in (
                    "asset_id",
                    "asset_type",
                    "rated_kva",
                    "utilisation_base",
                    "utilisation_with",
                    "headroom_kva",
                    "overloaded",
                    "upgrade",
                    "upgrade_cost_inr",
                    "confidence",
                )
            }
            for a in g["assets"][:8]
        ],
        "upgrades": g["upgrades"],
        "proxy_substations": [
            {k: p[k] for k in ("osm_id", "site_count", "added_peak_kw", "sanctioned_kw")}
            for p in g["proxy_substations"][:8]
        ],
        "connections": g["connections"],
        "evidence_id": g["evidence_id"],
        "summary": f"{g['mode']} ({g['confidence']}): plan peak {lc['peak_kw']} kW at "
        f"{lc['peak_hour']}:00",
    }


TOOLS = [
    Tool(
        "grid.impact",
        "grid",
        "Grid impact of the plan (module E): 24-hour load peak by segment, charger profile, "
        "and per grid asset either DISCOM-backed utilisation, headroom and upgrade costs, or "
        "a proximity-only estimate (load added at each nearest OSM substation).",
        RunOnly,
        grid_impact,
    ),
    Tool(
        "sizing.size_site",
        "sizing",
        "Charger sizing for sites of the plan (default: all): chargers, kW, sessions/day, "
        "queueing (Erlang-C) and simulated waits, connector guns, grid connection type.",
        SitesIn,
        size_sites,
    ),
    Tool(
        "energy.optimise",
        "energy",
        "Solar + battery options per site (grid only / PV / PV+battery / PV+battery on LT): "
        "capex, annual cost, recommended option and its effect on connection and OPEX. "
        "Up to 10 sites per call.",
        SitesIn,
        energy,
    ),
    Tool(
        "grid.connection",
        "grid",
        "Grid proxy per site: nearest substation distance, data confidence, required "
        "connection (LT/HT), sanctioned kW, transformer and connection cost.",
        SitesIn,
        grid_connection,
    ),
    Tool(
        "finance.calculate",
        "finance",
        "Per-site finance (capex, NPV/IRR base case and P10/P50/P90, cases, payback, top "
        "sensitivities) and portfolio totals against the budget. Optional subsidy.",
        FinanceIn,
        finance,
    ),
]

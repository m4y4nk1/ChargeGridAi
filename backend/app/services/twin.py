"""Digital-twin scenarios across onboarded regions (Phase 12):

- `add_chargers`: "add 10,000 fast chargers": where they'd go (largest gaps first), how
  much unserved demand they'd serve, and whether demand runs out first;
- `adoption`: "adoption +30%": public demand, unserved gap and charge points needed
  under the adoption multiplier vs the base case;
- `grid_constraints`: "where does the grid constrain?": place the chargers as above
  and total the connected load at each nearest substation; with DISCOM ratings the
  substations are assessed for headroom, otherwise ranked by added load (proximity
  proxy, confidence LOW).

Overlapping regions (PMR sits inside the Mumbai–Pune corridor) are merged per H3
cell; a cell belongs to the smallest region that contains it, so it's counted once.
Demand rests on synthetic registrations until VAHAN data is loaded: every result says
so and carries confidence NONE for demand-derived figures.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.assumptions import Assumptions, load_config
from app.core.regions import Region, all_regions, get_region, scenario_key
from app.db.models.grid import GridAssetRated
from app.db.repositories.demand import supply_assumptions
from app.engines.grid.headroom import AssetRating, SiteLoad, UpgradeRules, assess
from app.engines.optimisation.mclp import charger_kw
from app.engines.supply_gap import additional_charge_points, charge_point_kwh_per_day
from app.engines.twin import allocate
from app.services.demand_run import scenario_name

KINDS = ("add_chargers", "adoption", "grid_constraints")


class TwinError(ValueError):
    pass


def _regions(ids: list[str] | None) -> list[Region]:
    try:
        regions = [get_region(r) for r in ids] if ids else all_regions()
    except KeyError as exc:
        raise TwinError(str(exc)) from exc
    missing = [r.name for r in regions if not r.onboarded]
    if missing:
        raise TwinError(f"Not onboarded yet (no data): {', '.join(missing)}")
    # Smallest first: a shared cell is claimed by the most specific region.
    return sorted(
        regions, key=lambda r: (r.bbox.max_lat - r.bbox.min_lat) * (r.bbox.max_lng - r.bbox.min_lng)
    )


async def _cells(
    session: AsyncSession, regions: list[Region], scenario: str, year: int, column: str
) -> dict[str, tuple[str, float]]:
    """h3 -> (region id, value) for a column of h3_cell_feature, deduplicated."""
    if column not in ("gap_kwh", "public_kwh_p50", "ev_stock_p50"):
        raise TwinError("unknown column")
    out: dict[str, tuple[str, float]] = {}
    for r in regions:
        rows = (
            await session.execute(
                text(
                    f"select f.h3::text, f.{column} from h3_cell_feature f "
                    "join region_cell rc on rc.h3 = f.h3 and rc.region_id = :r "
                    "where f.scenario = :s and f.year = :y"
                ),
                {"r": r.id, "s": scenario_key(r.id, scenario), "y": year},
            )
        ).all()
        if not rows:
            raise TwinError(
                f"No '{scenario}' demand for {r.name} in {year}; run "
                f"`make demand REGION={r.id}` (with MULTIPLIER for adoption what-ifs)"
            )
        for cell, value in rows:
            out.setdefault(cell, (r.id, float(value or 0.0)))
    return out


def _caveats(synthetic: bool) -> list[str]:
    out = [
        "Screening estimate on the demand grid: chargers are placed on cells, not sites; "
        "a site plan needs a planning run.",
    ]
    if synthetic:
        out.append(
            "Demand rests on SYNTHETIC vehicle registrations (no VAHAN data loaded): "
            "demand-derived figures illustrate the method, confidence NONE."
        )
    return out


async def _synthetic(session: AsyncSession) -> bool:
    from app.db.repositories.demand import latest_run

    run = await latest_run(session)
    return True if run is None else run.synthetic


async def _placement(
    session: AsyncSession, params: dict[str, Any], regions: list[Region]
) -> tuple[dict[str, Any], dict[str, Any], dict[str, tuple[str, float]]]:
    n = int(params.get("n", 10_000))
    cls = str(params.get("charger_class", "DC_60"))
    year = int(params.get("year", 2030))
    case = str(params.get("case", "base"))
    if not 1 <= n <= 1_000_000:
        raise TwinError("n must be between 1 and 1,000,000")
    try:
        kw = charger_kw(cls)
    except (IndexError, ValueError) as exc:
        raise TwinError(f"unknown charger class {cls}") from exc
    supply, _ = supply_assumptions()
    per_charger = charge_point_kwh_per_day(kw, supply)
    cells = await _cells(session, regions, scenario_name(case, 1.0, None), year, "gap_kwh")
    alloc = allocate(
        {c: v for c, (_, v) in cells.items()}, n, per_charger, int(params.get("max_per_cell", 20))
    )
    by_region: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for cell, (rid, gap) in cells.items():
        by_region[rid]["gap_before"] += max(gap, 0.0)
        by_region[rid]["chargers"] += alloc.chargers.get(cell, 0)
        by_region[rid]["served"] += alloc.served_kwh.get(cell, 0.0)
    placement = {
        "n_requested": n,
        "charger_class": cls,
        "kwh_per_charger_per_day": round(per_charger, 1),
        "placed": alloc.placed,
        "unplaced": alloc.unplaced,
        "demand_limited": alloc.unplaced > 0,
        "cells_used": len(alloc.chargers),
        "gap_before_kwh": round(alloc.gap_before),
        "gap_after_kwh": round(alloc.gap_after),
        "served_added_kwh": round(alloc.gap_before - alloc.gap_after),
        "gap_closed_share": round(1 - alloc.gap_after / alloc.gap_before, 4)
        if alloc.gap_before
        else None,
        "by_region": [
            {
                "region_id": rid,
                "name": get_region(rid).name,
                "chargers": int(v["chargers"]),
                "gap_before_kwh": round(v["gap_before"]),
                "served_added_kwh": round(v["served"]),
                "gap_after_kwh": round(v["gap_before"] - v["served"]),
            }
            for rid, v in by_region.items()
        ],
    }
    top = sorted(alloc.chargers.items(), key=lambda kv: -alloc.served_kwh[kv[0]])[:15]
    if top:
        coords = {
            h: (lat, lng)
            for h, lat, lng in (
                await session.execute(
                    text(
                        "select h3::text, ST_Y(ST_Centroid(geom)), ST_X(ST_Centroid(geom)) "
                        "from h3_cell where h3::text = any(:cells)"
                    ),
                    {"cells": [c for c, _ in top]},
                )
            ).all()
        }
        placement["top_cells"] = [
            {
                "h3": c,
                "region_id": cells[c][0],
                "chargers": k,
                "served_added_kwh": round(alloc.served_kwh[c]),
                "lat": round(coords[c][0], 5) if c in coords else None,
                "lng": round(coords[c][1], 5) if c in coords else None,
            }
            for c, k in top
        ]
    return placement, {"kw": kw, "chargers": alloc.chargers}, cells


async def _grid(session: AsyncSession, kw: float, chargers: dict[str, int]) -> dict[str, Any]:
    """Connected load per nearest substation; DISCOM-assessed where rated assets exist."""
    a = Assumptions()
    unit = load_config("costs", "unit_costs.yaml")["capex"]
    diversity = a.get(unit["diversity_factor"], "costs.capex.diversity_factor")
    grid_cfg = load_config("grid.yaml")
    max_ss = a.get(grid_cfg["matching"]["max_substation_distance_m"], "grid.max_substation_m")
    if not chargers:
        return {"mode": "proxy", "substations": [], "confidence": "LOW"}
    cells, counts = list(chargers), [chargers[c] for c in chargers]
    rows = (
        await session.execute(
            text(
                "with c as (select unnest(cast(:cells as text[])) h3, unnest(cast(:n as int[])) n) "
                "select s.id, s.voltage_kv, sum(c.n), min(s.d), s.rated_id "
                "from c join h3_cell hc on hc.h3::text = c.h3 "
                "cross join lateral (select g.osm_type || g.osm_id id, g.voltage_kv, "
                "  ST_Distance(g.geom::geography, ST_Centroid(hc.geom)::geography) d, "
                "  (select r.id from grid_asset_rated r where r.asset_type = 'substation' "
                "   and ST_DWithin(r.geom::geography, ST_Centroid(hc.geom)::geography, :max_ss) "
                "   order by r.geom <-> ST_Centroid(hc.geom) limit 1) rated_id "
                "  from grid_asset g where g.type = 'substation' "
                "  order by g.geom <-> ST_Centroid(hc.geom) limit 1) s "
                "group by s.id, s.voltage_kv, s.rated_id"
            ),
            {"cells": cells, "n": counts, "max_ss": max_ss},
        )
    ).all()
    subs = []
    rated_load: dict[str, float] = defaultdict(float)
    for sid, voltage, n, dist, rated_id in rows:
        load_kw = float(n) * kw * diversity
        subs.append(
            {
                "osm_id": sid,
                "voltage_kv": voltage,
                "chargers": int(n),
                "added_load_kw": round(load_kw),
                "max_distance_m": round(float(dist)),
                "discom_asset": rated_id,
            }
        )
        if rated_id:
            rated_load[rated_id] += load_kw
    subs.sort(key=lambda s: -s["added_load_kw"])
    assessed: list[dict[str, Any]] = []
    if rated_load:
        fin = load_config("finance.yaml")
        up = grid_cfg["upgrades"]
        rules = UpgradeRules(
            up["transformer_sizes_kva"],
            a.get(up["transformer_inr_per_kva"], "grid.transformer_inr_per_kva"),
            a.get(up["transformer_fixed_inr"], "grid.transformer_fixed_inr"),
        )
        default_limit = a.get(grid_cfg["loading"]["default_loading_limit"], "grid.loading_limit")
        assets = {
            g.id: g
            for g in (
                await session.execute(
                    select(GridAssetRated).where(GridAssetRated.id.in_(list(rated_load)))
                )
            ).scalars()
        }
        for aid, load_kw in rated_load.items():
            g = assets[aid]
            res = assess(
                AssetRating(
                    g.id,
                    g.asset_type,
                    g.rated_kva,
                    g.loading_limit or default_limit,
                    g.base_peak_kva,
                    g.measured_on,
                    g.base_hourly_kva,
                ),
                [SiteLoad("twin", [load_kw] * 24, load_kw)],
                float(fin["power_factor"]),
                1.0,
                rules,
                datetime.now(UTC).date(),
            )
            assessed.append(
                {
                    "asset_id": aid,
                    "name": g.name,
                    "rated_kva": g.rated_kva,
                    "utilisation_base": round(res.utilisation_base, 3),
                    "utilisation_with": round(res.utilisation_with, 3),
                    "overloaded": res.overloaded,
                    "upgrade": res.upgrade,
                    "confidence": res.confidence,
                }
            )
        assessed.sort(key=lambda x: -float(x["utilisation_with"]))
    mode = (
        "discom"
        if rated_load and len(rated_load) == len(subs)
        else ("mixed" if rated_load else "proxy")
    )
    return {
        "mode": mode,
        "confidence": "LOW" if mode == "proxy" else "MEDIUM",
        "note": "Load = chargers x kW x diversity factor (connected/sanctioned load). "
        + (
            "Substation ratings unknown: ranked by added load only (proximity proxy)."
            if mode == "proxy"
            else "Rated substations assessed for headroom."
        ),
        "diversity_factor": diversity,
        "substations": subs[:20],
        "substations_total": len(subs),
        "assessed": assessed,
        "constrained": [x for x in assessed if x["overloaded"]],
    }


async def run_twin(session: AsyncSession, kind: str, params: dict[str, Any]) -> dict[str, Any]:
    if kind not in KINDS:
        raise TwinError(f"kind must be one of {KINDS}")
    regions = _regions(params.get("regions"))
    synthetic = await _synthetic(session)
    result: dict[str, Any] = {
        "kind": kind,
        "regions": [r.id for r in regions],
        "scope_note": "Covers the onboarded regions only; other regions are registered but "
        "have no data yet (see /readiness).",
        "caveats": _caveats(synthetic),
        "demand_confidence": "NONE" if synthetic else "LOW",
    }
    if kind in ("add_chargers", "grid_constraints"):
        placement, raw, _ = await _placement(session, params, regions)
        result["placement"] = placement
        if kind == "grid_constraints":
            result["grid"] = await _grid(session, raw["kw"], raw["chargers"])
    else:
        mult = float(params.get("multiplier", 1.3))
        year = int(params.get("year", 2030))
        case = str(params.get("case", "base"))
        if not 0.1 <= mult <= 5:
            raise TwinError("multiplier must be between 0.1 and 5")
        base_s, what_s = scenario_name(case, 1.0, None), scenario_name(case, mult, None)
        out = {}
        for col, label in (
            ("public_kwh_p50", "public_kwh"),
            ("gap_kwh", "gap_kwh"),
            ("ev_stock_p50", "evs"),
        ):
            b = await _cells(session, regions, base_s, year, col)
            w = await _cells(session, regions, what_s, year, col)
            out[label] = {
                "base": round(sum(max(v, 0) for _, v in b.values())),
                "what_if": round(sum(max(v, 0) for _, v in w.values())),
            }
            out[label]["delta"] = out[label]["what_if"] - out[label]["base"]
        supply, _ = supply_assumptions()
        out["charge_points_to_close_gap"] = {
            "base": additional_charge_points(out["gap_kwh"]["base"], supply),
            "what_if": additional_charge_points(out["gap_kwh"]["what_if"], supply),
        }
        out["charge_points_to_close_gap"]["delta"] = (
            out["charge_points_to_close_gap"]["what_if"] - out["charge_points_to_close_gap"]["base"]
        )
        result["adoption"] = {
            "multiplier": mult,
            "year": year,
            "case": case,
            "new_charge_point_kw": supply.new_charge_point_kw,
            **out,
        }
    return result


async def save_twin(
    session: AsyncSession,
    kind: str,
    params: dict[str, Any],
    result: dict[str, Any],
    org_id: uuid.UUID | None,
) -> uuid.UUID:
    from app.db.models.twin import TwinRun

    run = TwinRun(kind=kind, params=params, result=result, org_id=org_id)
    session.add(run)
    await session.flush()
    return run.id

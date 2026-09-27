"""Grid Impact (module E, Section 9.11): what a plan does to the grid.

- charger profile by class and host type;
- the plan's 24-hour load curve, stacked by charger class and by vehicle segment;
- per grid asset: base vs with-charging utilisation, headroom and upgrades, when the
  DISCOM has supplied ratings and loadings (make ingest-discom);
- otherwise a proximity-only estimate: each site's nearest OSM substation and the
  load it would add there, with no utilisation (confidence LOW).

Sites on an LT connection draw from the nearest rated distribution transformer
within range, else a rated substation; HT sites take supply at 11 kV from a
substation. Asset rows never leave the API as map features (RESTRICTED_COMMERCIAL).
"""

from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.assumptions import Assumptions, load_config
from app.db.models.grid import GridAssetRated
from app.db.models.planning import (
    CandidateSite,
    Optimisation,
    OptimisationSite,
    PlanningRun,
)
from app.db.models.provenance import DatasetSnapshot, Evidence
from app.engines.grid.headroom import AssetRating, SiteLoad, UpgradeRules, assess
from app.engines.grid.load_profile import HOURS, site_hourly_kw, split_by_share, stack, total
from app.services.site_economics import EconomicsError, site_economics

METHOD = "grid_impact_v1"
CONFIDENCE_ORDER = ["NONE", "LOW", "MEDIUM", "HIGH"]


class GridImpactError(RuntimeError):
    pass


def _r(x: float, d: int = 1) -> float:
    return round(float(x), d)


async def _nearest_osm_substation(session: AsyncSession, lat: float, lng: float) -> dict[str, Any]:
    row = (
        await session.execute(
            text(
                "select osm_type || osm_id::text, voltage_kv, confidence, "
                "ST_Distance(geom::geography, ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)"
                "::geography) from grid_asset where type = 'substation' "
                "order by geom <-> ST_SetSRID(ST_MakePoint(:lng, :lat), 4326) limit 1"
            ),
            {"lat": lat, "lng": lng},
        )
    ).first()
    if row is None:
        return {"osm_id": None, "voltage_kv": None, "distance_m": None, "confidence": "NONE"}
    return {
        "osm_id": row[0],
        "voltage_kv": row[1],
        "confidence": row[2] or "LOW",
        "distance_m": round(float(row[3])),
    }


async def _rated_asset(
    session: AsyncSession, lat: float, lng: float, kind: str, max_m: float
) -> tuple[GridAssetRated, float] | None:
    row = (
        await session.execute(
            select(
                GridAssetRated,
                text(
                    "ST_Distance(grid_asset_rated.geom::geography, "
                    "ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography)"
                ),
            )
            .where(
                GridAssetRated.asset_type == kind,
                text(
                    "ST_DWithin(grid_asset_rated.geom::geography, "
                    "ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography, :max_m)"
                ),
            )
            .order_by(text("grid_asset_rated.geom <-> ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)"))
            .limit(1),
            {"lat": lat, "lng": lng, "max_m": max_m},
        )
    ).first()
    return (row[0], float(row[1])) if row else None


async def grid_impact(session: AsyncSession, run_id: uuid.UUID) -> dict[str, Any]:
    run = await session.get(PlanningRun, run_id)
    if run is None:
        raise GridImpactError("Run not found")
    base_id = (run.funnel.get("optimisation") or {}).get("base_id")
    if not base_id:
        raise GridImpactError("This run has no optimised plan yet")
    opt = await session.get(Optimisation, uuid.UUID(base_id))
    assert opt is not None
    cfg = load_config("grid.yaml")
    fin = load_config("finance.yaml")
    a = Assumptions()
    max_dt = a.get(cfg["matching"]["max_transformer_distance_m"], "grid.max_transformer_m")
    max_ss = a.get(cfg["matching"]["max_substation_distance_m"], "grid.max_substation_m")
    default_limit = a.get(cfg["loading"]["default_loading_limit"], "grid.loading_limit")
    coincidence = a.get(cfg["loading"]["coincidence_factor"], "grid.coincidence_factor")
    eff = a.get(fin["charger_efficiency"], "finance.charger_efficiency")
    pf = float(fin["power_factor"])
    up = cfg["upgrades"]
    rules = UpgradeRules(
        transformer_sizes_kva=up["transformer_sizes_kva"],
        transformer_inr_per_kva=a.get(
            up["transformer_inr_per_kva"], "grid.transformer_inr_per_kva"
        ),
        transformer_fixed_inr=a.get(up["transformer_fixed_inr"], "grid.transformer_fixed_inr"),
    )

    rows = (
        await session.execute(
            select(
                OptimisationSite.site_id,
                CandidateSite.name,
                CandidateSite.host_type,
                text("ST_Y(candidate_site.geom)"),
                text("ST_X(candidate_site.geom)"),
            )
            .join(CandidateSite, CandidateSite.id == OptimisationSite.site_id)
            .where(OptimisationSite.optimisation_id == opt.id)
        )
    ).all()

    sites: list[dict[str, Any]] = []
    loads: dict[str, list[float]] = {}
    by_class: list[tuple[str, list[float]]] = []
    by_segment: list[tuple[str, list[float]]] = []
    skipped: dict[str, str] = {}
    asset_sites: dict[str, list[SiteLoad]] = defaultdict(list)
    assets: dict[str, tuple[GridAssetRated, float]] = {}
    proxy_groups: dict[str, dict[str, Any]] = {}
    snapshot_ids: set[uuid.UUID] = set()
    for site_id, name, host, lat, lng in rows:
        try:
            config = await site_economics(session, opt.id, site_id, subsidy=False)
        except EconomicsError as exc:
            skipped[str(site_id)] = str(exc)
            continue
        z = config.sizing
        hourly = site_hourly_kw(z["served_kwh_per_day"], z["simulation"]["hourly_utilisation"], eff)
        sid = str(site_id)
        loads[sid] = hourly
        by_class.append((z["charger_class"], hourly))
        by_segment.extend(
            split_by_share(hourly, {s["segment"]: s["kwh_share"] for s in z["segments"]}).items()
        )
        connection = z["electrical"]["connection"]
        sanctioned = float(z["electrical"]["sanctioned_kw"])
        osm = await _nearest_osm_substation(session, float(lat), float(lng))
        matched = None
        if connection == "LT":
            matched = await _rated_asset(session, float(lat), float(lng), "transformer", max_dt)
        if matched is None:
            matched = await _rated_asset(session, float(lat), float(lng), "substation", max_ss)
        grid: dict[str, Any]
        if matched is not None:
            asset, dist = matched
            assets[asset.id] = matched
            asset_sites[asset.id].append(SiteLoad(sid, hourly, sanctioned))
            if asset.snapshot_id:
                snapshot_ids.add(asset.snapshot_id)
            grid = {
                "mode": "discom",
                "asset_id": asset.id,
                "asset_type": asset.asset_type,
                "distance_m": round(dist),
            }
        else:
            grid = {"mode": "proxy", "confidence": osm["confidence"] if osm["osm_id"] else "NONE"}
            if osm["osm_id"]:
                g = proxy_groups.setdefault(
                    osm["osm_id"],
                    {
                        "osm_id": osm["osm_id"],
                        "voltage_kv": osm["voltage_kv"],
                        "sites": [],
                        "hourly_kw": [0.0] * HOURS,
                        "sanctioned_kw": 0.0,
                    },
                )
                g["sites"].append(sid)
                g["sanctioned_kw"] += sanctioned
                g["hourly_kw"] = [g["hourly_kw"][h] + hourly[h] for h in range(HOURS)]
        sites.append(
            {
                "site_id": sid,
                "name": name,
                "host_type": host,
                "charger_class": z["charger_class"],
                "chargers": z["chargers"],
                "total_kw": z["total_kw"],
                "sanctioned_kw": sanctioned,
                "connection": connection,
                "transformer_required": z["electrical"]["transformer_required"],
                "connection_cost_inr": z["electrical"]["connection_cost_inr"],
                "daily_kwh": _r(sum(hourly)),
                "peak_kw": _r(max(hourly)),
                "nearest_osm_substation": osm,
                "grid": grid,
            }
        )

    today = datetime.now(UTC).date()
    medium_days = int(cfg["confidence"]["medium_max_age_days"])
    high_days = int(cfg["confidence"]["high_max_age_days"])
    assessments = []
    for asset_id, (asset, _) in assets.items():
        result = assess(
            AssetRating(
                asset_id=asset.id,
                asset_type=asset.asset_type,
                rated_kva=asset.rated_kva,
                loading_limit=asset.loading_limit or default_limit,
                base_peak_kva=asset.base_peak_kva,
                measured_on=asset.measured_on,
                base_hourly_kva=asset.base_hourly_kva,
            ),
            asset_sites[asset_id],
            pf,
            coincidence,
            rules,
            today,
            medium_days,
            high_days,
        )
        d: dict[str, Any] = {
            k: (round(v, 3) if isinstance(v, float) else v) for k, v in vars(result).items()
        }
        d["name"] = asset.name
        d["voltage_kv"] = asset.voltage_kv
        d["measured_on"] = str(asset.measured_on) if asset.measured_on else None
        if d["hourly_with_kva"]:
            d["hourly_with_kva"] = [_r(v) for v in d["hourly_with_kva"]]
        assessments.append(d)
    for s in sites:
        if s["grid"]["mode"] == "discom":
            match = next(x for x in assessments if x["asset_id"] == s["grid"]["asset_id"])
            s["grid"]["confidence"] = match["confidence"]

    classes = stack(by_class)
    segments = stack(by_segment)
    plan_total = total(classes) if classes else [0.0] * HOURS
    covered = sum(1 for s in sites if s["grid"]["mode"] == "discom")
    mode = "proxy" if covered == 0 else "discom" if covered == len(sites) else "mixed"
    site_conf = [s["grid"].get("confidence", "LOW") for s in sites]
    confidence = min(site_conf, key=CONFIDENCE_ORDER.index) if site_conf else "NONE"
    if mode == "proxy":
        badge = "Proximity-only estimate: no DISCOM loading data for these sites"
    elif mode == "mixed":
        badge = f"DISCOM data for {covered} of {len(sites)} sites; the rest are proximity-only"
    else:
        badge = "DISCOM asset ratings and loadings"
    transformer_upgrades = [x for x in assessments if x["upgrade_cost_inr"] is not None]
    out: dict[str, Any] = {
        "run_id": str(run_id),
        "optimisation_id": str(opt.id),
        "method": METHOD,
        "mode": mode,
        "badge": badge,
        "confidence": confidence,
        "sites": sites,
        "skipped": skipped,
        "charger_profile": {
            "by_class": {
                cls: {
                    "sites": sum(1 for s in sites if s["charger_class"] == cls),
                    "chargers": sum(s["chargers"] for s in sites if s["charger_class"] == cls),
                    "kw": sum(s["total_kw"] for s in sites if s["charger_class"] == cls),
                }
                for cls in sorted({s["charger_class"] for s in sites})
            },
            "by_host_type": dict(Counter(s["host_type"] for s in sites)),
        },
        "load_curve": {
            "unit": "kW (hourly average)",
            "by_class": {k: [_r(v) for v in p] for k, p in classes.items()},
            "by_segment": {k: [_r(v) for v in p] for k, p in segments.items()},
            "total": [_r(v) for v in plan_total],
            "peak_kw": _r(max(plan_total)),
            "peak_hour": int(max(range(HOURS), key=lambda h: plan_total[h])),
            "daily_kwh": _r(sum(plan_total)),
            "sanctioned_kw_total": _r(sum(s["sanctioned_kw"] for s in sites)),
        },
        "assets": sorted(assessments, key=lambda x: -x["utilisation_with"]),
        "utilisation": (
            {
                "base": [x["utilisation_base"] for x in assessments],
                "with_charging": [x["utilisation_with"] for x in assessments],
            }
            if assessments
            else None
        ),
        "upgrades": (
            {
                "assets_overloaded": sum(1 for x in assessments if x["overloaded"]),
                "transformers_to_replace": len(transformer_upgrades),
                "transformer_cost_inr": sum(x["upgrade_cost_inr"] for x in transformer_upgrades),
                "substations_needing_study": sum(
                    1 for x in assessments if x["overloaded"] and x["asset_type"] == "substation"
                ),
            }
            if assessments
            else None
        ),
        "proxy_substations": sorted(
            (
                {
                    **g,
                    "hourly_kw": None,
                    "added_peak_kw": _r(max(g["hourly_kw"])),
                    "sanctioned_kw": _r(g["sanctioned_kw"]),
                    "site_count": len(g["sites"]),
                }
                for g in proxy_groups.values()
            ),
            key=lambda g: -g["added_peak_kw"],
        ),
        "connections": {
            "ht_sites": sum(1 for s in sites if s["connection"] == "HT"),
            "lt_sites": sum(1 for s in sites if s["connection"] == "LT"),
            "transformers_required": sum(1 for s in sites if s["transformer_required"]),
            "connection_cost_inr": sum(s["connection_cost_inr"] for s in sites),
        },
        "assumptions": {
            "power_factor": pf,
            "charger_efficiency": eff,
            "coincidence_factor": coincidence,
            "default_loading_limit": default_limit,
            "max_transformer_distance_m": max_dt,
            "max_substation_distance_m": max_ss,
        },
        "placeholders": sorted(a.placeholders_in_use),
    }
    sources = []
    if snapshot_ids:
        sources = [
            str(s.id)
            for s in (
                await session.execute(
                    select(DatasetSnapshot).where(DatasetSnapshot.id.in_(snapshot_ids))
                )
            ).scalars()
        ]
    evidence = (
        await session.execute(
            select(Evidence).where(
                Evidence.subject_type == "optimisation",
                Evidence.subject_id == str(opt.id),
                Evidence.metric == "grid:plan_peak_kw",
            )
        )
    ).scalar_one_or_none()
    if evidence is None:
        evidence = Evidence(
            subject_type="optimisation",
            subject_id=str(opt.id),
            metric="grid:plan_peak_kw",
            unit="kW",
            method=METHOD,
            run_id=run.id,
        )
        session.add(evidence)
    evidence.value_num = out["load_curve"]["peak_kw"]
    evidence.snapshot_ids = [uuid.UUID(s) for s in sources]
    evidence.confidence = confidence
    await session.commit()
    out["evidence_id"] = str(evidence.id)
    return out

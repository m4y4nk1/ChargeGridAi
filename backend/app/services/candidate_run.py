"""Planning run steps: resolve_data -> candidates -> feasibility (Phase 4) ->
catchments -> scoring (Phase 5, `scoring_run`) -> optimise -> why_not -> strategies
(Phase 6, `optimisation_run`) -> funnel.

Later phases append score/optimise/size/... to the same run (Section 5
request flow). Each step logs to `planning_run.progress`, which the SSE
endpoint streams.
"""

from __future__ import annotations

import gc
import json
import math
import os
import uuid
from collections import Counter
from datetime import UTC, datetime
from typing import Any

import h3
from geoalchemy2.shape import from_shape
from shapely import wkt as shapely_wkt
from shapely.geometry import Point as ShapelyPoint
from shapely.geometry.base import BaseGeometry
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.assumptions import Assumptions, load_config
from app.core.regions import get_region
from app.db.models.planning import (
    CandidateSite,
    FeasibilityResult,
    PlanningRun,
    Scenario,
    SiteFeature,
)
from app.db.models.provenance import DatasetSnapshot, Evidence
from app.db.repositories.demand import latest_run
from app.engines.candidates import (
    ROADSIDE,
    HostOption,
    RawCandidate,
    dedupe,
    haversine_m,
    pick_host,
)
from app.engines.feasibility import Measurements, RuleConfig, evaluate
from app.services.demand_run import scenario_name
from app.services.optimisation_run import optimise_run
from app.services.scoring_run import score_run, scoring_config
from app.services.site_economics import economics_step

METHOD = "candidates_feasibility_v1"
RULE_CODES = ("F01", "F02", "F03", "F04", "F05", "F06", "F07")


class RunError(RuntimeError):
    pass


def is_dc(scenario: Scenario) -> bool:
    return any(c.upper().startswith("DC") for c in scenario.charger_classes)


async def _progress(
    session: AsyncSession, run: PlanningRun, step: str, status: str, **detail: Any
) -> None:
    run.progress = [
        *run.progress,
        {"step": step, "status": status, "at": datetime.now(UTC).isoformat(), **detail},
    ]
    await session.commit()


# --- resolve --------------------------------------------------------------------------


def _rule_config(a: Assumptions) -> tuple[RuleConfig, dict[str, Any]]:
    rules = load_config("policy", "rules.yaml")["feasibility_rules"]
    cfg = RuleConfig(
        enabled={code: bool(rules[code]["enabled"]) for code in RULE_CODES},
        max_road_distance_m=a.get(
            rules["F02"]["max_road_distance_m"], "rules.F02.max_road_distance_m"
        ),
        min_dc_spacing_m=a.get(rules["F03"]["min_spacing_m"], "rules.F03.min_spacing_m"),
        served_access_ratio=a.get(
            rules["F03"]["served_access_ratio"], "rules.F03.served_access_ratio"
        ),
        min_grid_confidence=str(rules["F04"]["min_grid_confidence"]),
        max_substation_distance_m=a.get(
            rules["F04"]["max_substation_distance_m"], "rules.F04.max_substation_distance_m"
        ),
        max_arterial_distance_m=a.get(
            rules["F05"]["max_arterial_distance_m"], "rules.F05.max_arterial_distance_m"
        ),
    )
    return cfg, rules


# --- candidates --------------------------------------------------------------------------


def _bbox_params(region_id: str | None = None) -> dict[str, float]:
    box = get_region(region_id).bbox
    return {"w": box.min_lng, "s": box.min_lat, "e": box.max_lng, "n": box.max_lat}


async def _host_pois(
    session: AsyncSession, cfg: dict[str, Any], region_id: str
) -> list[HostOption]:
    hosts_cfg = cfg["host_pois"]
    near_classes = sorted({c for h in hosts_cfg.values() for c in h.get("near_road_classes", [])})
    rows = (
        await session.execute(
            text(
                "select p.osm_type || p.osm_id as poi_id, p.category, p.name, p.attrs, "
                "ST_Y(p.geom) as lat, ST_X(p.geom) as lng, "
                "(select ST_Distance(r.geom::geography, p.geom::geography) from road_segment r "
                " where r.class = any(:near) order by r.geom <-> p.geom limit 1) as near_road_m "
                "from poi p where p.category = any(:cats) "
                "and ST_Intersects(p.geom, ST_MakeEnvelope(:w, :s, :e, :n, 4326))"
            ),
            {"cats": list(hosts_cfg), "near": near_classes, **_bbox_params(region_id)},
        )
    ).mappings()
    hosts = []
    for r in rows:
        rule = hosts_cfg[r["category"]]
        attrs = r["attrs"] or {}
        if any(attrs.get(k) == v for k, v in rule.get("exclude_tags", {}).items()):
            continue
        if "near_road_classes" in rule and (
            r["near_road_m"] is None or r["near_road_m"] > rule["within_m"]
        ):
            continue
        hosts.append(HostOption(r["poi_id"], rule["host_type"], r["name"], r["lat"], r["lng"], 0.0))
    return hosts


def _hosts_near(
    hosts: list[HostOption], lat: float, lng: float, within_m: float
) -> list[HostOption]:
    # Cheap degree prefilter, then exact distance.
    d_lat = within_m / 111_000
    d_lng = d_lat / max(math.cos(math.radians(lat)), 0.1)
    out = []
    for h in hosts:
        if abs(h.lat - lat) <= d_lat and abs(h.lng - lng) <= d_lng:
            d = haversine_m(lat, lng, h.lat, h.lng)
            if d <= within_m:
                out.append(HostOption(h.poi_id, h.host_type, h.name, h.lat, h.lng, d))
    return out


# Merge each named/numbered highway into continuous lines (UTM 43N, metres),
# then sample a point every `step` metres along each part.
_CORRIDOR_SQL = """
with lines as (
  select coalesce(ref, name) as corridor,
         (ST_Dump(ST_LineMerge(ST_Union(ST_Transform(geom, 32643))))).geom as g
  from road_segment
  where class = any(:classes) and coalesce(ref, name) is not null
    and ST_Intersects(geom, ST_MakeEnvelope(:w, :s, :e, :n, 4326))
  group by 1
)
select corridor,
       ST_Y(ST_Transform(pt.geom, 4326)) as lat,
       ST_X(ST_Transform(pt.geom, 4326)) as lng
from lines,
     lateral ST_Dump(
       ST_LineInterpolatePoints(g, least(1.0, :step / ST_Length(g)), true)
     ) pt
where ST_Length(g) >= :min_len
"""

_SNAP_SQL = """
select t.i, ST_Y(nr.cp) as lat, ST_X(nr.cp) as lng, nr.d
from unnest(cast(:lats as float8[]), cast(:lngs as float8[]))
     with ordinality as t(lat, lng, i),
     lateral (
       select ST_ClosestPoint(r.geom, pt.g) as cp,
              ST_Distance(r.geom::geography, pt.g::geography) as d
       from (select ST_SetSRID(ST_MakePoint(t.lng, t.lat), 4326) as g) pt,
            lateral (select geom from road_segment order by geom <-> pt.g limit 1) r
     ) nr
"""


async def _corridor_samples(
    session: AsyncSession, cfg: dict[str, Any], region_id: str
) -> list[dict[str, Any]]:
    c = cfg["corridors"]
    rows = (
        await session.execute(
            text(_CORRIDOR_SQL),
            {
                "classes": c["road_classes"],
                "step": float(c["sample_every_m"]),
                "min_len": float(c["min_length_m"]),
                **_bbox_params(region_id),
            },
        )
    ).mappings()
    return [dict(r) for r in rows]


async def _snap_to_roads(
    session: AsyncSession, points: list[tuple[float, float]], within_m: float
) -> list[tuple[float, float, float] | None]:
    """Closest point on the nearest drivable road for each (lat, lng), or None if too far."""
    if not points:
        return []
    rows = (
        await session.execute(
            text(_SNAP_SQL),
            {"lats": [p[0] for p in points], "lngs": [p[1] for p in points]},
        )
    ).all()
    out: list[tuple[float, float, float] | None] = [None] * len(points)
    for i, lat, lng, d in rows:
        if d <= within_m:
            out[int(i) - 1] = (lat, lng, d)
    return out


async def _gap_cells(
    session: AsyncSession,
    region_id: str,
    year: int,
    demand_scenario: str,
    top_share: float,
    area: BaseGeometry | None = None,
) -> tuple[list[tuple[str, float]], int]:
    rows = (
        await session.execute(
            text(
                "select f.h3::text, f.gap_kwh from h3_cell_feature f "
                "join region_cell c on c.h3 = f.h3 "
                "where c.region_id = :region and f.scenario = :sc and f.year = :year "
                "and f.gap_kwh > 0 order by f.gap_kwh desc"
            ),
            {"region": region_id, "sc": demand_scenario, "year": year},
        )
    ).all()
    if area is not None:
        # The top share of the cells the corridor actually covers.
        rows = [r for r in rows if area.contains(ShapelyPoint(*reversed(h3.cell_to_latlng(r[0]))))]
    n = math.ceil(len(rows) * top_share)
    return [(r[0], float(r[1])) for r in rows[:n]], len(rows)


async def corridor_area(session: AsyncSession, corridor: dict[str, Any]) -> BaseGeometry:
    """The corridor constraint (a GeoJSON line + buffer in metres) as a polygon."""
    wkt = (
        await session.execute(
            text(
                "select ST_AsText(ST_Buffer(ST_SetSRID(ST_GeomFromGeoJSON(:line), 4326)"
                "::geography, :buffer)::geometry)"
            ),
            {"line": json.dumps(corridor["line"]), "buffer": float(corridor["buffer_m"])},
        )
    ).scalar_one()
    return shapely_wkt.loads(wkt)


async def _generate(
    session: AsyncSession, scenario: Scenario, demand_scenario: str, cfg: dict[str, Any]
) -> tuple[list[RawCandidate], dict[str, Any]]:
    corridor = scenario.constraints.get("corridor")
    area = await corridor_area(session, corridor) if corridor else None
    hosts = await _host_pois(session, cfg, scenario.region_id)
    raw: list[RawCandidate] = [
        RawCandidate("poi", h.lat, h.lng, h.host_type, h.poi_id, h.name) for h in hosts
    ]

    corridor_cfg = cfg["corridors"]
    samples = await _corridor_samples(session, cfg, scenario.region_id)
    for s in samples:
        host = pick_host(
            _hosts_near(hosts, s["lat"], s["lng"], corridor_cfg["snap_to_host_within_m"]),
            corridor_cfg["host_preference"],
            corridor_cfg["snap_to_host_within_m"],
        )
        if host:
            raw.append(
                RawCandidate(
                    "corridor",
                    host.lat,
                    host.lng,
                    host.host_type,
                    host.poi_id,
                    host.name,
                    {
                        "corridor": s["corridor"],
                        "snapped_to": "host",
                        "snap_m": round(host.distance_m),
                    },
                )
            )
        else:
            raw.append(
                RawCandidate(
                    "corridor",
                    s["lat"],
                    s["lng"],
                    ROADSIDE,
                    None,
                    None,
                    {"corridor": s["corridor"], "snapped_to": "road", "snap_m": 0},
                )
            )

    gap_cfg = cfg["gap_cells"]
    top_cells, gap_cell_count = await _gap_cells(
        session,
        scenario.region_id,
        scenario.target_year,
        demand_scenario,
        gap_cfg["top_share"],
        area,
    )
    radius = gap_cfg["snap_within_m"]
    unhosted: list[tuple[str, float, float, float]] = []
    for cell, gap in top_cells:
        lat, lng = h3.cell_to_latlng(cell)
        host = pick_host(_hosts_near(hosts, lat, lng, radius), [], radius)
        if host:
            raw.append(
                RawCandidate(
                    "gap_cell",
                    host.lat,
                    host.lng,
                    host.host_type,
                    host.poi_id,
                    host.name,
                    {
                        "h3": cell,
                        "gap_kwh": round(gap, 1),
                        "snapped_to": "host",
                        "snap_m": round(host.distance_m),
                    },
                )
            )
        else:
            unhosted.append((cell, gap, lat, lng))
    snapped = await _snap_to_roads(session, [(u[2], u[3]) for u in unhosted], radius)
    unsnappable = 0
    for (cell, gap, _, _), road in zip(unhosted, snapped, strict=True):
        if road is None:
            unsnappable += 1
            continue
        raw.append(
            RawCandidate(
                "gap_cell",
                road[0],
                road[1],
                ROADSIDE,
                None,
                None,
                {
                    "h3": cell,
                    "gap_kwh": round(gap, 1),
                    "snapped_to": "road",
                    "snap_m": round(road[2]),
                },
            )
        )

    for site in scenario.constraints.get("user_sites", []):
        raw.append(
            RawCandidate(
                "user",
                float(site["lat"]),
                float(site["lng"]),
                "USER_SITE",
                None,
                site.get("name"),
                {"supplied_by": "user"},
            )
        )

    outside = 0
    if area is not None:
        # A corridor request keeps only candidates inside its buffer (and the user's own).
        inside = [c for c in raw if c.origin == "user" or area.contains(ShapelyPoint(c.lng, c.lat))]
        outside = len(raw) - len(inside)
        raw = inside

    theoretical = {
        "total": len(raw) + unsnappable,
        "by_origin": dict(Counter(c.origin for c in raw)),
        "gap_cells_considered": len(top_cells),
        "gap_cells_with_gap": gap_cell_count,
        "gap_cells_unsnappable": unsnappable,
        "corridor_samples": len(samples),
        **(
            {"outside_corridor": outside, "corridor_buffer_m": corridor["buffer_m"]}
            if corridor
            else {}
        ),
    }
    return raw, theoretical


# --- feasibility ---------------------------------------------------------------------------

_MEASURE_SQL = """
select c.id,
  ST_Y(c.geom) as lat, ST_X(c.geom) as lng,
  (select coalesce(
       json_agg(json_build_object('class', lc.class, 'tag', lc.tag, 'name', lc.name)), '[]')
     from landcover lc where lc.restricted and ST_Intersects(lc.geom, c.geom)) as restricted,
  (select coalesce(array_agg(distinct lc.class), '{}')
     from landcover lc where not lc.restricted and ST_Intersects(lc.geom, c.geom)) as landuse,
  (select r.class from road_segment r order by r.geom <-> c.geom limit 1) as road_class,
  (select min(ST_Distance(x.geom::geography, c.geom::geography)) from
     (select geom from road_segment r order by r.geom <-> c.geom limit 3) x) as road_m,
  (select min(ST_Distance(x.geom::geography, c.geom::geography)) from
     (select geom from road_segment r where r.class = any(:arterial)
      order by r.geom <-> c.geom limit 3) x) as arterial_m,
  (select ST_Distance(s.geom::geography, c.geom::geography) from charging_station s
     where exists (select 1 from evse e where e.station_id = s.id and e.current = 'DC')
     order by s.geom <-> c.geom limit 1) as dc_m,
  sub.d as substation_m, sub.confidence as grid_confidence
from candidate_site c
left join lateral (
  select ST_Distance(g.geom::geography, c.geom::geography) as d, g.confidence
  from grid_asset g where g.type = 'substation' order by g.geom <-> c.geom limit 1
) sub on true
where c.created_by_run = :run
"""

# Confidence of a rejection follows the data the rule depends on.
_RULE_CONFIDENCE = {"F01": "MEDIUM", "F02": "MEDIUM", "F04": "LOW", "F05": "MEDIUM"}


async def _cell_values(
    session: AsyncSession, region_id: str, year: int, demand_scenario: str
) -> dict[str, dict[str, float | None]]:
    rows = (
        await session.execute(
            text(
                "select f.h3::text, f.access_ratio, f.gap_kwh, f.public_kwh_p50, f.ev_stock_p50 "
                "from h3_cell_feature f join region_cell c on c.h3 = f.h3 "
                "where c.region_id = :region and f.scenario = :sc and f.year = :year"
            ),
            {"region": region_id, "sc": demand_scenario, "year": year},
        )
    ).all()
    return {
        r[0]: {"access_ratio": r[1], "gap_kwh": r[2], "public_kwh_p50": r[3], "ev_stock_p50": r[4]}
        for r in rows
    }


# --- run --------------------------------------------------------------------------------------


async def _input_snapshots(
    session: AsyncSession, demand_evidence_id: uuid.UUID
) -> tuple[list[uuid.UUID], list[uuid.UUID]]:
    """(all run inputs, OSM feature import only).

    All inputs = the demand run's snapshots plus the latest OSM import (roads, POIs,
    land cover, substations). Rules that only measure OSM geometry cite the latter.
    """
    demand_snaps = (
        await session.execute(
            select(Evidence.snapshot_ids).where(Evidence.id == demand_evidence_id)
        )
    ).scalar_one()
    osm = (
        await session.execute(
            select(DatasetSnapshot.id)
            .where(DatasetSnapshot.source_id == "osm", DatasetSnapshot.granularity == "feature")
            .order_by(DatasetSnapshot.retrieved_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    osm_only = [osm] if osm else []
    return sorted(set(demand_snaps) | set(osm_only)), osm_only


async def create_run(
    session: AsyncSession, scenario_id: uuid.UUID, stop_after: str | None = None
) -> PlanningRun:
    if await session.get(Scenario, scenario_id) is None:
        raise RunError("Unknown scenario")
    run = PlanningRun(
        scenario_id=scenario_id,
        status="queued",
        progress=[],
        funnel={},
        inputs={"stop_after": stop_after} if stop_after else {},
    )
    session.add(run)
    await session.commit()
    return run


async def execute_run(session: AsyncSession, run_id: uuid.UUID) -> PlanningRun:
    run = await session.get(PlanningRun, run_id)
    if run is None:
        raise RunError(f"Unknown run {run_id}")
    scenario = await session.get(Scenario, run.scenario_id)
    assert scenario is not None
    run.status, run.started_at = "running", datetime.now(UTC)
    await session.commit()

    step = "resolve_data"
    stop_after = (run.inputs or {}).get("stop_after")
    try:
        # --- resolve_data
        await _progress(session, run, step, "started")
        a = Assumptions()
        rule_cfg, rules = _rule_config(a)
        cand_cfg = load_config("candidates.yaml")
        demand_scenario = scenario_name(
            scenario.adoption_case, scenario.adoption_multiplier, scenario.region_id
        )
        demand = await latest_run(session, demand_scenario)
        if demand is None:
            raise RunError(
                f"No demand forecast for scenario '{demand_scenario}'. Run `make demand`"
                + (
                    f" MULTIPLIER={scenario.adoption_multiplier:g}"
                    if scenario.adoption_multiplier != 1.0
                    else ""
                )
            )
        cells = await _cell_values(
            session, scenario.region_id, scenario.target_year, demand_scenario
        )
        if not cells:
            raise RunError(f"No {demand_scenario} demand rows for {scenario.target_year}")
        dc = is_dc(scenario)
        snapshot_ids, osm_snapshot_ids = await _input_snapshots(
            session, uuid.UUID(demand.evidence_id)
        )
        # F03 depends on demand (access ratio) and existing stations; the rest measure
        # OSM geometry only, so their evidence cites just that import.
        rule_snapshots = {code: osm_snapshot_ids for code in ("F01", "F02", "F04", "F05")}
        # YAML dates and other non-JSON values are stored as strings.
        run.inputs = _json_safe(
            {
                "scenario": {
                    "id": str(scenario.id),
                    "name": scenario.name,
                    "region_id": scenario.region_id,
                    "target_year": scenario.target_year,
                    "adoption_case": scenario.adoption_case,
                    "adoption_multiplier": scenario.adoption_multiplier,
                    "charger_classes": scenario.charger_classes,
                    "dc": dc,
                    "user_sites": len(scenario.constraints.get("user_sites", [])),
                },
                "demand_scenario": demand_scenario,
                "demand_run_id": demand.run_id,
                "demand_synthetic": demand.synthetic,
                "candidates_config": cand_cfg,
                "rules": {code: rules[code] for code in RULE_CODES},
                "resolved_thresholds": rule_cfg.__dict__,
                "placeholders_in_use": a.placeholders_in_use,
                "stop_after": stop_after,
            }
        )
        run.snapshot_ids = snapshot_ids
        run.model_versions = {
            "candidates": "v1",
            "feasibility": "v1",
            "scoring": "v1",
            "optimisation": "mclp_v1",
            "sizing": "erlang_c_simpy_v1",
            "finance": "mc_v1",
        }
        run.git_sha = os.environ.get("GIT_SHA", "unknown")
        await _progress(session, run, step, "completed")

        # --- candidates
        step = "candidates"
        await _progress(session, run, step, "started")
        raw, theoretical = await _generate(session, scenario, demand_scenario, cand_cfg)
        kept = dedupe(raw, cand_cfg["dedupe_within_m"], cand_cfg["origin_priority"])
        sites: list[tuple[CandidateSite, dict[str, Any]]] = []
        for k in kept:
            c = k.candidate
            site = CandidateSite(
                created_by_run=run.id,
                region_id=scenario.region_id,
                geom=from_shape(ShapelyPoint(c.lng, c.lat), srid=4326),
                origin=c.origin,
                merged_origins=k.merged_origins,
                host_poi_id=c.host_poi_id,
                host_type=c.host_type,
                name=c.name,
                status="proposed",
            )
            session.add(site)
            sites.append((site, {**c.detail, "merged_count": k.merged_count}))
        await session.flush()
        candidates_funnel = {
            "total": len(kept),
            "by_origin": dict(Counter(k.candidate.origin for k in kept)),
            "merged_within_m": cand_cfg["dedupe_within_m"],
            "removed_as_duplicates": len(raw) - len(kept),
        }
        await _progress(session, run, step, "completed", candidates=len(kept))

        # --- feasibility
        step = "feasibility"
        await _progress(session, run, step, "started")
        arterial = rules["F05"]["arterial_road_classes"]
        measured = {
            r["id"]: r
            for r in (
                await session.execute(text(_MEASURE_SQL), {"run": run.id, "arterial": arterial})
            )
            .mappings()
            .all()
        }
        res = 8
        reasons: Counter[str] = Counter()
        evidence: list[Evidence] = []
        for site, detail in sites:
            r = measured[site.id]
            cell = h3.latlng_to_cell(r["lat"], r["lng"], res)
            cv = cells.get(cell, {})
            m = Measurements(
                restricted_hits=r["restricted"],
                landuse=list(r["landuse"] or []),
                nearest_road_m=_round(r["road_m"]),
                nearest_road_class=r["road_class"],
                nearest_arterial_m=_round(r["arterial_m"]),
                nearest_dc_station_m=_round(r["dc_m"]),
                cell_access_ratio=cv.get("access_ratio"),
                nearest_substation_m=_round(r["substation_m"]),
                grid_confidence=r["grid_confidence"] or "NONE",
            )
            verdict = evaluate(m, rule_cfg, dc)
            site.status = "feasible" if verdict.passed else "rejected"
            session.add(
                FeasibilityResult(
                    site_id=site.id,
                    passed=verdict.passed,
                    reason_codes=verdict.reason_codes,
                    details=verdict.details,
                )
            )
            session.add(
                SiteFeature(
                    site_id=site.id,
                    year=scenario.target_year,
                    scenario=demand_scenario,
                    features={
                        **m.__dict__,
                        "h3": cell,
                        "cell_gap_kwh": cv.get("gap_kwh"),
                        "cell_public_kwh_p50": cv.get("public_kwh_p50"),
                        "cell_ev_stock_p50": cv.get("ev_stock_p50"),
                        "origin_detail": detail,
                    },
                    snapshot_ids=snapshot_ids,
                )
            )
            for code in verdict.reason_codes:
                reasons[code] += 1
                d = verdict.details[code]
                measured_value = d.get("measured")
                evidence.append(
                    Evidence(
                        subject_type="candidate_site",
                        subject_id=str(site.id),
                        metric=f"feasibility:{code}",
                        value_num=measured_value
                        if isinstance(measured_value, int | float)
                        else None,
                        value_text=json.dumps(d, default=str),
                        unit=d.get("unit"),
                        method=METHOD,
                        snapshot_ids=rule_snapshots.get(code, snapshot_ids),
                        confidence=(
                            "NONE"
                            if code == "F03" and demand.synthetic
                            else _RULE_CONFIDENCE.get(code, "LOW")
                        ),
                        run_id=run.id,
                    )
                )
        session.add_all(evidence)
        feasible = sum(1 for s, _ in sites if s.status == "feasible")
        feasibility_funnel = {
            "feasible": {
                "total": feasible,
                "by_origin": dict(Counter(s.origin for s, _ in sites if s.status == "feasible")),
            },
            "rejected": {"total": len(sites) - feasible, "by_reason": dict(reasons)},
        }
        await _progress(session, run, step, "completed", feasible=feasible)
        # The worker has ~1 GB to spare: drop this step's objects before scoring and
        # optimisation load theirs.
        del raw, kept, sites, measured, cells, evidence
        run, scenario = await _release(session, run_id)

        # --- catchments, scoring
        step = "scoring"
        profile = scenario.weight_profile_id or scoring_config()["default_profile"]
        scoring = await score_run(
            session,
            run,
            region_id=scenario.region_id,
            target_year=scenario.target_year,
            demand_scenario=demand_scenario,
            demand_synthetic=bool(demand.synthetic),
            weight_profile=profile,
            snapshot_ids=snapshot_ids,
            a=a,
            progress=_progress,
        )
        if scoring["scored"]["total"] == 0 and scoring["scored"]["unscored"] > 0:
            why = scoring["scored"].get("unscored_reasons") or {}
            raise RunError(
                "No candidate could be scored "
                f"({', '.join(f'{k}: {v}' for k, v in why.items()) or 'no catchments'}). "
                "Is the routing engine (Valhalla) running?"
            )

        run, scenario = await _release(session, run_id)

        if stop_after == "scoring":
            # Human-in-the-loop (Section 10.1): the user confirms before the costly part.
            optimised = {
                "selected": None,
                "optimisation": {
                    "skipped": "stopped after scoring; POST /runs/{id}/optimise to continue",
                    "awaiting_confirmation": True,
                },
            }
        else:
            step = "optimise"
            optimised = await _optimise_and_value(session, run_id, run, scenario)

        # --- funnel
        run.funnel = {
            "theoretical": theoretical,
            "candidates": candidates_funnel,
            **feasibility_funnel,
            "scored": scoring["scored"],
            "shortlisted": scoring["shortlisted"],
            "selected": optimised["selected"],
            "optimisation": optimised["optimisation"],
        }
        run.status, run.finished_at = "succeeded", datetime.now(UTC)
        await _progress(session, run, "run", "completed")
        return run
    except Exception as exc:
        await session.rollback()
        run = await session.get(PlanningRun, run_id)
        assert run is not None
        run.status, run.finished_at, run.error = "failed", datetime.now(UTC), str(exc)
        await _progress(session, run, step, "failed", error=str(exc))
        raise


async def _optimise_and_value(
    session: AsyncSession, run_id: uuid.UUID, run: PlanningRun, scenario: Scenario
) -> dict[str, Any]:
    """Optimise, why-not, strategies, then size and value the plan's sites."""
    optimised = await optimise_run(session, run, scenario, _progress)
    base_id = optimised["optimisation"].get("base_id")
    if base_id:
        run, scenario = await _release(session, run_id)
        optimised["optimisation"]["economics"] = await economics_step(
            session, run, uuid.UUID(base_id), _progress
        )
    return optimised


async def continue_optimisation(session: AsyncSession, run_id: uuid.UUID) -> PlanningRun:
    """Resume a run stopped after scoring (POST /runs/{id}/optimise)."""
    run = await session.get(PlanningRun, run_id)
    if run is None:
        raise RunError(f"Unknown run {run_id}")
    if not (run.funnel.get("optimisation") or {}).get("awaiting_confirmation"):
        raise RunError("This run isn't waiting for confirmation")
    scenario = await session.get(Scenario, run.scenario_id)
    assert scenario is not None
    run.status, run.finished_at = "running", None
    await session.commit()
    try:
        optimised = await _optimise_and_value(session, run_id, run, scenario)
        run = await session.get(PlanningRun, run_id)
        assert run is not None
        run.funnel = {
            **run.funnel,
            "selected": optimised["selected"],
            "optimisation": optimised["optimisation"],
        }
        run.status, run.finished_at = "succeeded", datetime.now(UTC)
        await _progress(session, run, "run", "completed")
        return run
    except Exception as exc:
        await session.rollback()
        run = await session.get(PlanningRun, run_id)
        assert run is not None
        run.status, run.finished_at, run.error = "failed", datetime.now(UTC), str(exc)
        await _progress(session, run, "optimise", "failed", error=str(exc))
        raise


async def _release(session: AsyncSession, run_id: uuid.UUID) -> tuple[PlanningRun, Scenario]:
    """Commit, forget every loaded object, and reload the run and its scenario."""
    await session.commit()
    session.expunge_all()
    gc.collect()
    run = await session.get(PlanningRun, run_id)
    assert run is not None
    scenario = await session.get(Scenario, run.scenario_id)
    assert scenario is not None
    return run, scenario


def _json_safe(value: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = json.loads(json.dumps(value, default=str))
    return safe


def _round(x: float | None) -> float | None:
    return None if x is None else round(float(x), 1)

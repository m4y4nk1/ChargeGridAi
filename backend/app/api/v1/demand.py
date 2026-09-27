"""Area Planner and demand/gap layers (Section 11: /regions/{id}/kpis,
/regions/{id}/ev-projection, /demand/h3, /gap/h3)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.regions import get_region, scenario_key
from app.db.models.provenance import Evidence
from app.db.repositories import demand as repo
from app.db.session import get_session

router = APIRouter(tags=["demand"])


class Band(BaseModel):
    p10: float
    p50: float
    p90: float


class RunInfo(BaseModel):
    run_id: str
    evidence_id: str
    synthetic_registrations: bool
    registration_sources: list[str]
    observed_window: list[str]
    scenarios: dict[str, float]
    placeholders_in_use: list[str]
    allocation_status: str | None
    rto_catchments: dict[str, Any]
    stations_with_assumed_count: int
    charge_points_with_assumed_kw: int


class AreaKpis(BaseModel):
    area: dict[str, Any]
    year: int
    scenario: str
    population: float
    population_year: int | None
    current_evs: float
    current_evs_as_of: str
    current_evs_by_segment: dict[str, float]
    projected_evs: Band
    projected_evs_by_segment: dict[str, Band]
    existing: dict[str, int]
    public_kwh_per_day: Band
    sessions_per_day: float
    gap_kwh_per_day: float
    additional_charge_points: int
    evs_per_public_charge_point: float | None
    confidence: dict[str, str]
    run: RunInfo
    evidence_ids: list[str]


class ProjectionPoint(BaseModel):
    year: int
    p10: float
    p50: float
    p90: float


class Projection(BaseModel):
    area: dict[str, Any]
    scenario: str
    segment: str | None
    observed: list[ProjectionPoint]
    forecast: list[ProjectionPoint]
    observed_through: str
    confidence: str


class H3Layer(BaseModel):
    year: int
    scenario: str
    metric: str
    unit: str
    h3: list[str]
    value: list[float]
    confidence: str


async def _require_run(session: AsyncSession, scenario: str | None = None) -> repo.DemandRun:
    run = await repo.latest_run(session, scenario)
    if run is None:
        if scenario is not None and await repo.latest_run(session) is not None:
            raise HTTPException(422, f"No demand run has produced scenario {scenario!r}")
        raise HTTPException(409, "No demand run yet — run `make demand`")
    return run


def _region(region: str | None) -> str:
    try:
        return get_region(region).id
    except KeyError as exc:
        raise HTTPException(422, str(exc)) from exc


def _run_info(run: repo.DemandRun) -> RunInfo:
    c = run.config
    return RunInfo(
        run_id=run.run_id,
        evidence_id=run.evidence_id,
        synthetic_registrations=run.synthetic,
        registration_sources=c["registration_sources"],
        observed_window=c["observed_window"],
        scenarios=c["scenarios"],
        placeholders_in_use=c["placeholders_in_use"],
        allocation_status=c.get("allocation_status"),
        rto_catchments=c["rto_catchments"],
        stations_with_assumed_count=c.get("stations_with_assumed_count", 0),
        charge_points_with_assumed_kw=c.get("charge_points_with_assumed_kw", 0),
    )


@router.get("/demand/run", response_model=RunInfo)
async def get_demand_run(session: AsyncSession = Depends(get_session)) -> RunInfo:
    return _run_info(await _require_run(session))


@router.get("/regions/{area_id}/kpis", response_model=AreaKpis)
async def get_area_kpis(
    area_id: int,
    year: int = Query(..., ge=2020, le=2035),
    scenario: str = "base",
    region: str | None = Query(None, description="config/regions.yaml id; default region"),
    session: AsyncSession = Depends(get_session),
) -> AreaKpis:
    rid = _region(region)
    sc, hist = scenario_key(rid, scenario), scenario_key(rid, "history")
    run = await _require_run(session, sc)
    area = await repo.area_info(session, area_id, rid)
    if area is None:
        raise HTTPException(404, "Region not found")

    totals = await repo.area_totals(session, area_id, [hist, sc], rid)
    forecast = totals.get((sc, year))
    if forecast is None:
        raise HTTPException(422, f"No {scenario} forecast for {year}")
    observed_through = run.config["observed_window"][1][:7]
    current_year = int(observed_through[:4])
    current = totals.get((hist, current_year), {})
    population, population_year = await repo.area_population(session, area_id, rid)
    existing = await repo.area_charge_points(session, area_id)
    gap = float(forecast["gap"] or 0.0)
    segments_now = await repo.area_segments(session, area_id, hist, current_year, rid)
    segments_then = await repo.area_segments(session, area_id, sc, year, rid)

    fusion_evidence = (
        (
            await session.execute(
                select(Evidence.id).where(Evidence.subject_type == "charger_fusion")
            )
        )
        .scalars()
        .all()
    )
    population_evidence = (
        await session.execute(
            select(Evidence)
            .where(Evidence.subject_type == "region", Evidence.metric.like("population_%"))
            .order_by(Evidence.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    demand_conf = "NONE" if run.synthetic else "LOW"
    return AreaKpis(
        area=area,
        year=year,
        scenario=scenario,
        population=round(population),
        population_year=population_year,
        current_evs=round(float(current.get("p50") or 0.0)),
        current_evs_as_of=observed_through,
        current_evs_by_segment={k: round(v[1]) for k, v in segments_now.items()},
        projected_evs=Band(
            p10=round(forecast["p10"]), p50=round(forecast["p50"]), p90=round(forecast["p90"])
        ),
        projected_evs_by_segment={
            k: Band(p10=round(v[0]), p50=round(v[1]), p90=round(v[2]))
            for k, v in segments_then.items()
        },
        existing=existing,
        public_kwh_per_day=Band(
            p10=round(forecast["kwh_p10"]),
            p50=round(forecast["kwh_p50"]),
            p90=round(forecast["kwh_p90"]),
        ),
        sessions_per_day=round(float(forecast["sessions"] or 0.0)),
        gap_kwh_per_day=round(gap),
        additional_charge_points=repo.charge_points_for_gap(gap),
        evs_per_public_charge_point=(
            round(forecast["p50"] / existing["charge_points_incl_assumed"], 1)
            if existing["charge_points_incl_assumed"]
            else None
        ),
        confidence={
            "population": population_evidence.confidence if population_evidence else "NONE",
            "ev_stock": "NONE" if run.synthetic else "MEDIUM",
            "demand": demand_conf,
            "gap": demand_conf,
            "existing_supply": "LOW",
        },
        run=_run_info(run),
        evidence_ids=[
            run.evidence_id,
            *([str(population_evidence.id)] if population_evidence else []),
            *(str(i) for i in fusion_evidence),
        ],
    )


@router.get("/regions/{area_id}/ev-projection", response_model=Projection)
async def get_ev_projection(
    area_id: int,
    scenario: str = "base",
    segment: str | None = None,
    region: str | None = Query(None, description="config/regions.yaml id; default region"),
    session: AsyncSession = Depends(get_session),
) -> Projection:
    rid = _region(region)
    sc, hist = scenario_key(rid, scenario), scenario_key(rid, "history")
    run = await _require_run(session, sc)
    area = await repo.area_info(session, area_id, rid)
    if area is None:
        raise HTTPException(404, "Region not found")

    wanted = [hist, sc]
    if segment is None:
        bands = {
            key: [t["p10"], t["p50"], t["p90"]]
            for key, t in (await repo.area_totals(session, area_id, wanted, rid)).items()
        }
    else:
        bands = await repo.area_segment_series(session, area_id, wanted, segment, rid)
        if not bands:
            raise HTTPException(422, f"Unknown segment {segment}")

    def points(sc: str) -> list[ProjectionPoint]:
        return [
            ProjectionPoint(year=year, p10=round(b[0]), p50=round(b[1]), p90=round(b[2]))
            for (s, year), b in sorted(bands.items(), key=lambda kv: kv[0][1])
            if s == sc
        ]

    series = {"history": points(hist), scenario: points(sc)}
    return Projection(
        area=area,
        scenario=scenario,
        segment=segment,
        observed=series["history"],
        forecast=series[scenario],
        observed_through=run.config["observed_window"][1][:7],
        confidence="NONE" if run.synthetic else "MEDIUM",
    )


async def _layer(
    session: AsyncSession, year: int, scenario: str, metric: str, unit: str, region: str | None
) -> H3Layer:
    rid = _region(region)
    sc = scenario_key(rid, scenario)
    run = await _require_run(session, sc)
    cells, values = await repo.h3_layer(session, year, sc, metric, rid)
    return H3Layer(
        year=year,
        scenario=scenario,
        metric=metric,
        unit=unit,
        h3=cells,
        value=values,
        confidence="NONE" if run.synthetic else "LOW",
    )


@router.get("/demand/h3", response_model=H3Layer)
async def get_demand_h3(
    year: int,
    scenario: str = "base",
    region: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> H3Layer:
    return await _layer(session, year, scenario, "public_kwh_p50", "kWh/day", region)


@router.get("/gap/h3", response_model=H3Layer)
async def get_gap_h3(
    year: int,
    scenario: str = "base",
    region: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> H3Layer:
    return await _layer(session, year, scenario, "gap_kwh", "kWh/day", region)

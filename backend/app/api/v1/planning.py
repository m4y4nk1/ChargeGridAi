"""Scenarios and planning runs (Section 11): /scenarios, /runs, /runs/{id}/events (SSE),
/runs/{id}/funnel, /runs/{id}/sites, /runs/{id}/rejected, /runs/{id}/ranking,
/weight-profiles."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Principal, audit, get_principal
from app.core.regions import default_region_id, get_region
from app.db.models.planning import (
    CandidateSite,
    FeasibilityResult,
    PlanningRun,
    RunSite,
    Scenario,
    SiteCatchment,
    SiteFeature,
)
from app.db.models.provenance import Evidence
from app.db.session import async_session_factory, get_session
from app.engines.scoring import SUB_SCORES, validate_weights
from app.services.candidate_run import create_run
from app.services.jobs import continue_planning, failure_reason, start_planning
from app.services.scoring_run import rank_with, scoring_config, weight_profiles

router = APIRouter(tags=["planning"])

CHARGER_CLASSES = ("AC_7", "AC_22", "DC_30", "DC_60", "DC_120", "DC_180", "DC_240", "DC_350")


class UserSite(BaseModel):
    name: str | None = None
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


class Corridor(BaseModel):
    """Restrict candidates to a buffer around a route (e.g. Mumbai-Pune Expressway)."""

    line: dict[str, Any]  # GeoJSON LineString, lng/lat
    buffer_m: int = Field(default=5000, ge=500, le=50000)
    label: str | None = None

    @field_validator("line")
    @classmethod
    def _is_linestring(cls, v: dict[str, Any]) -> dict[str, Any]:
        coords = v.get("coordinates")
        if v.get("type") != "LineString" or not isinstance(coords, list) or len(coords) < 2:
            raise ValueError("corridor line must be a GeoJSON LineString with 2+ points")
        return v


class ScenarioIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    target_year: int = Field(ge=2026, le=2035)
    adoption_case: Literal["slow", "base", "fast"] = "base"
    adoption_multiplier: float = Field(default=1.0, gt=0, le=5)
    charger_classes: list[str] = Field(min_length=1)
    budget_inr: int | None = Field(default=None, ge=0)
    max_sites: int | None = Field(default=None, ge=1, le=500)
    weight_profile: str | None = None
    region_id: str | None = None
    corridor: Corridor | None = None
    user_sites: list[UserSite] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def _inside_region(self) -> ScenarioIn:
        try:
            region = get_region(self.region_id)
        except KeyError as exc:
            raise ValueError(str(exc)) from exc
        if not region.onboarded:
            raise ValueError(
                f"{region.name} is registered but not onboarded yet (no data loaded); "
                "see its readiness score and docs/onboarding-regions.md"
            )
        self.region_id = region.id
        for site in self.user_sites:
            if not region.bbox.contains(site.lat, site.lng):
                raise ValueError(
                    f"site ({site.lat}, {site.lng}) is outside the modelled region {region.name}"
                )
        return self

    @field_validator("weight_profile")
    @classmethod
    def _known_profile(cls, v: str | None) -> str | None:
        if v is not None and v not in weight_profiles():
            raise ValueError(f"unknown weight profile; use {sorted(weight_profiles())}")
        return v

    @field_validator("charger_classes")
    @classmethod
    def _known_classes(cls, v: list[str]) -> list[str]:
        unknown = sorted(set(v) - set(CHARGER_CLASSES))
        if unknown:
            raise ValueError(f"unknown charger classes {unknown}; use {list(CHARGER_CLASSES)}")
        return v


class ScenarioOut(BaseModel):
    id: str
    name: str
    region_id: str
    target_year: int
    adoption_case: str
    adoption_multiplier: float
    charger_classes: list[str]
    budget_inr: int | None
    max_sites: int | None
    weight_profile: str
    user_sites: list[dict[str, Any]]
    created_at: datetime


class RunIn(BaseModel):
    scenario_id: uuid.UUID
    # "scoring": stop before optimisation, e.g. to ask the user to confirm the spend
    # (agents' human-in-the-loop step); continue with POST /runs/{id}/optimise.
    stop_after: Literal["scoring"] | None = None


class RunOut(BaseModel):
    id: str
    scenario: ScenarioOut
    status: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    progress: list[dict[str, Any]]
    funnel: dict[str, Any]
    error: str | None
    placeholders_in_use: list[str]
    demand_synthetic: bool | None
    scoring: dict[str, Any] | None


def _scenario_out(s: Scenario) -> ScenarioOut:
    return ScenarioOut(
        id=str(s.id),
        name=s.name,
        region_id=s.region_id,
        target_year=s.target_year,
        adoption_case=s.adoption_case,
        adoption_multiplier=s.adoption_multiplier,
        charger_classes=s.charger_classes,
        budget_inr=s.budget_inr,
        max_sites=(s.constraints or {}).get("max_sites"),
        weight_profile=s.weight_profile_id or scoring_config()["default_profile"],
        user_sites=s.constraints.get("user_sites", []),
        created_at=s.created_at,
    )


async def _run_out(session: AsyncSession, run: PlanningRun) -> RunOut:
    scenario = await session.get(Scenario, run.scenario_id)
    assert scenario is not None
    return RunOut(
        id=str(run.id),
        scenario=_scenario_out(scenario),
        status=run.status,
        created_at=run.created_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
        progress=run.progress,
        funnel=run.funnel,
        error=run.error,
        placeholders_in_use=run.inputs.get("placeholders_in_use", []),
        demand_synthetic=run.inputs.get("demand_synthetic"),
        scoring=_scoring_summary(run),
    )


def _scoring_summary(run: PlanningRun) -> dict[str, Any] | None:
    s = run.inputs.get("scoring")
    if not s:
        return None
    keys = ("method", "weight_profile", "weights", "top_n", "shortlist", "catchment_minutes")
    return {
        **{k: s[k] for k in keys},
        "growth_window": s["growth_window"],
        "growth_note": s["growth_note"],
        "confidence": s["confidence"],
        "total_confidence": s["total_confidence"],
        "uninformative": s.get("uninformative", {}),
        "robustness": s["robustness"],
    }


async def _get_run(session: AsyncSession, run_id: uuid.UUID) -> PlanningRun:
    run = await session.get(PlanningRun, run_id)
    if run is None:
        raise HTTPException(404, "Run not found")
    await _reconcile(session, run)
    return run


async def _reconcile(session: AsyncSession, run: PlanningRun) -> None:
    """A worker killed mid-run (e.g. out of memory) can't mark its run failed; Celery
    still records the task as FAILURE, so fold that into the run when it is read."""
    if run.status not in ("queued", "running") or not run.task_id:
        return
    reason = await failure_reason(run.task_id)
    if reason is None:
        return
    run.status, run.finished_at = "failed", datetime.now(UTC)
    run.error = f"The worker stopped during this run ({reason})."
    step = run.progress[-1]["step"] if run.progress else "run"
    run.progress = [
        *run.progress,
        {"step": step, "status": "failed", "at": run.finished_at.isoformat(), "error": run.error},
    ]
    await session.commit()


# --- scenarios ------------------------------------------------------------------------


@router.post("/scenarios", response_model=ScenarioOut, status_code=201)
async def create_scenario(
    body: ScenarioIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> ScenarioOut:
    scenario = Scenario(
        name=body.name,
        region_id=body.region_id or default_region_id(),
        target_year=body.target_year,
        adoption_case=body.adoption_case,
        adoption_multiplier=body.adoption_multiplier,
        charger_classes=body.charger_classes,
        budget_inr=body.budget_inr,
        weight_profile_id=body.weight_profile,
        constraints={
            "user_sites": [s.model_dump() for s in body.user_sites],
            "max_sites": body.max_sites,
            **({"corridor": body.corridor.model_dump()} if body.corridor else {}),
        },
        owner_id=principal.user_id,
        org_id=principal.org_id,
    )
    session.add(scenario)
    await session.flush()
    await audit(
        session,
        principal,
        "scenario.create",
        "scenario",
        str(scenario.id),
        {"name": body.name, "region_id": scenario.region_id},
        request,
    )
    await session.commit()
    await session.refresh(scenario)
    return _scenario_out(scenario)


@router.get("/scenarios", response_model=list[ScenarioOut])
async def list_scenarios(
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> list[ScenarioOut]:
    rows = (
        await session.execute(
            select(Scenario)
            .where(Scenario.org_id == principal.org_id)
            .order_by(Scenario.created_at.desc())
        )
    ).scalars()
    return [_scenario_out(s) for s in rows]


@router.get("/scenarios/{scenario_id}", response_model=ScenarioOut)
async def get_scenario(
    scenario_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> ScenarioOut:
    scenario = await session.get(Scenario, scenario_id)
    if scenario is None:
        raise HTTPException(404, "Scenario not found")
    return _scenario_out(scenario)


# --- runs -----------------------------------------------------------------------------


@router.post("/runs", response_model=RunOut, status_code=202)
async def start_run(
    body: RunIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> RunOut:
    scenario = await session.get(Scenario, body.scenario_id)
    if scenario is None or scenario.org_id != principal.org_id:
        raise HTTPException(404, "Scenario not found")
    run = await create_run(session, body.scenario_id, body.stop_after)
    run.task_id = await start_planning(run.id)
    await audit(
        session,
        principal,
        "run.start",
        "planning_run",
        str(run.id),
        {"scenario_id": str(body.scenario_id), "stop_after": body.stop_after},
        request,
    )
    await session.commit()
    return await _run_out(session, run)


@router.post("/runs/{run_id}/optimise", response_model=RunOut, status_code=202)
async def continue_run(
    run_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> RunOut:
    """Confirm and continue a run started with stop_after="scoring"."""
    run = await _get_run(session, run_id)
    if not (run.funnel.get("optimisation") or {}).get("awaiting_confirmation"):
        raise HTTPException(409, "This run isn't waiting for confirmation")
    run.status = "queued"
    run.task_id = await continue_planning(run)
    await audit(session, principal, "run.optimise", "planning_run", str(run.id), {}, request)
    await session.commit()
    return await _run_out(session, run)


@router.get("/runs", response_model=list[RunOut])
async def list_runs(
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> list[RunOut]:
    runs = (
        await session.execute(
            select(PlanningRun)
            .join(Scenario, Scenario.id == PlanningRun.scenario_id)
            .where(Scenario.org_id == principal.org_id)
            .order_by(PlanningRun.created_at.desc())
            .limit(50)
        )
    ).scalars()
    out = []
    for r in runs:
        await _reconcile(session, r)
        out.append(await _run_out(session, r))
    return out


@router.get("/runs/{run_id}", response_model=RunOut)
async def get_run(run_id: uuid.UUID, session: AsyncSession = Depends(get_session)) -> RunOut:
    return await _run_out(session, await _get_run(session, run_id))


@router.get("/runs/{run_id}/funnel")
async def get_funnel(
    run_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    run = await _get_run(session, run_id)
    return {"status": run.status, "funnel": run.funnel}


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


@router.get("/runs/{run_id}/events")
async def run_events(run_id: uuid.UUID) -> StreamingResponse:
    """Server-sent progress events (Section 10.4 types) until the run finishes."""

    async def stream() -> AsyncIterator[str]:
        sent = 0
        while True:
            async with async_session_factory() as session:
                run = await session.get(PlanningRun, run_id)
                if run is not None:
                    await _reconcile(session, run)
            if run is None:
                yield _sse("error", {"message": "run not found"})
                return
            for entry in run.progress[sent:]:
                kind = {"started": "step_started", "failed": "error"}.get(
                    entry["status"], "progress"
                )
                yield _sse(kind, entry)
            sent = len(run.progress)
            if run.status in ("succeeded", "failed"):
                yield _sse(
                    "completed", {"status": run.status, "funnel": run.funnel, "error": run.error}
                )
                return
            await asyncio.sleep(0.5)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/runs/{run_id}/sites")
async def run_sites(
    run_id: uuid.UUID,
    status: Literal["proposed", "feasible", "rejected"] | None = None,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    await _get_run(session, run_id)
    query = (
        select(
            CandidateSite,
            func.ST_Y(CandidateSite.geom),
            func.ST_X(CandidateSite.geom),
            FeasibilityResult.reason_codes,
            RunSite.rank,
            RunSite.score_total,
            RunSite.selected,
        )
        .outerjoin(FeasibilityResult, FeasibilityResult.site_id == CandidateSite.id)
        .outerjoin(RunSite, (RunSite.site_id == CandidateSite.id) & (RunSite.run_id == run_id))
        .where(CandidateSite.created_by_run == run_id)
    )
    if status:
        query = query.where(CandidateSite.status == status)
    rows = (await session.execute(query)).all()
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lng, lat]},
                "properties": {
                    "id": str(site.id),
                    "origin": site.origin,
                    "merged_origins": site.merged_origins,
                    "host_type": site.host_type,
                    "name": site.name,
                    "status": site.status,
                    "reason_codes": reasons or [],
                    "rank": rank,
                    "score_total": score,
                    "selected": bool(selected),
                    "label": "Candidate site — requires field verification",
                },
            }
            for site, lat, lng, reasons, rank, score, selected in rows
        ],
    }


@router.get("/runs/{run_id}/sites/{site_id}")
async def run_site_detail(
    run_id: uuid.UUID, site_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    row = (
        await session.execute(
            select(
                CandidateSite,
                func.ST_Y(CandidateSite.geom),
                func.ST_X(CandidateSite.geom),
                FeasibilityResult,
            )
            .outerjoin(FeasibilityResult, FeasibilityResult.site_id == CandidateSite.id)
            .where(CandidateSite.id == site_id, CandidateSite.created_by_run == run_id)
        )
    ).first()
    if row is None:
        raise HTTPException(404, "Site not found in this run")
    site, lat, lng, result = row
    features = (
        await session.execute(select(SiteFeature.features).where(SiteFeature.site_id == site_id))
    ).scalar_one_or_none()
    evidence_ids = (
        await session.execute(
            select(Evidence.id).where(
                Evidence.subject_type == "candidate_site", Evidence.subject_id == str(site_id)
            )
        )
    ).scalars()
    run_site = await session.get(RunSite, (run_id, site_id))
    run = await _get_run(session, run_id)
    return {
        "id": str(site.id),
        "label": "Candidate site — requires field verification",
        "lat": lat,
        "lng": lng,
        "origin": site.origin,
        "merged_origins": site.merged_origins,
        "host_type": site.host_type,
        "host_poi_id": site.host_poi_id,
        "name": site.name,
        "status": site.status,
        "feasibility": (
            {"passed": result.passed, "reason_codes": result.reason_codes, "rules": result.details}
            if result
            else None
        ),
        "features": features,
        "scoring": (
            {
                "rank": run_site.rank,
                "score_total": run_site.score_total,
                "sub_scores": run_site.sub_scores,
                "robustness": run_site.robustness,
                **run_site.detail,
                "run": _scoring_summary(run),
            }
            if run_site
            else None
        ),
        "plan": (
            {
                "optimisation_id": (run.funnel.get("optimisation") or {}).get("base_id"),
                "selected": run_site.selected,
                "phase_year": run_site.phase_year,
                "coverage_kwh": run_site.coverage_kwh,
                "served_population": run_site.served_population,
                "why_not": run_site.why_not,
            }
            if run_site and (run_site.selected or run_site.why_not)
            else None
        ),
        "evidence_ids": [str(i) for i in evidence_ids],
    }


@router.get("/runs/{run_id}/sites/{site_id}/catchment")
async def run_site_catchment(
    run_id: uuid.UUID, site_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    row = (
        await session.execute(
            select(SiteCatchment.minutes, func.ST_AsGeoJSON(SiteCatchment.geom, 6))
            .join(CandidateSite, CandidateSite.id == SiteCatchment.site_id)
            .where(SiteCatchment.site_id == site_id, CandidateSite.created_by_run == run_id)
        )
    ).first()
    if row is None:
        raise HTTPException(404, "No catchment for this site")
    minutes, geojson = row
    return {
        "type": "Feature",
        "properties": {"minutes": minutes, "mode": "drive", "source": "Valhalla (OSM)"},
        "geometry": json.loads(geojson),
    }


# --- scoring ----------------------------------------------------------------------------


@router.get("/weight-profiles")
async def list_weight_profiles() -> list[dict[str, Any]]:
    return list(weight_profiles().values())


def _parse_weights(
    profile: str | None, weights: str | None, default: str
) -> tuple[str, dict[str, float]]:
    if weights:
        try:
            pairs = dict(p.split(":", 1) for p in weights.split(",") if p.strip())
            return "custom", validate_weights({k.strip(): float(v) for k, v in pairs.items()})
        except ValueError as exc:
            raise HTTPException(
                422, f"weights must be 'sub_score:number,...' over {list(SUB_SCORES)}: {exc}"
            ) from exc
    profiles = weight_profiles()
    name = profile or default
    if name not in profiles:
        raise HTTPException(422, f"unknown weight profile; use {sorted(profiles)}")
    return name, profiles[name]["weights"]


@router.get("/runs/{run_id}/ranking")
async def run_ranking(
    run_id: uuid.UUID,
    profile: str | None = None,
    weights: str | None = Query(None, description="Custom weights, e.g. demand:0.5,grid:0.5"),
    top_n: int | None = Query(None, ge=1, le=500),
    limit: int = Query(100, ge=1, le=2000),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Feasible candidates ranked under the run's profile, another profile, or custom
    weights. Sub-scores are fixed per run; only the weighting changes."""
    run = await _get_run(session, run_id)
    summary = _scoring_summary(run)
    if summary is None:
        raise HTTPException(409, "This run has no scores (it failed or predates Phase 5)")
    name, w = _parse_weights(profile, weights, summary["weight_profile"])
    n = top_n or summary["top_n"]
    rows = (
        await session.execute(
            select(RunSite, CandidateSite)
            .join(CandidateSite, CandidateSite.id == RunSite.site_id)
            .where(RunSite.run_id == run_id, RunSite.score_total.is_not(None))
        )
    ).all()
    ranked = rank_with([rs.sub_scores for rs, _ in rows], w, n, summary["robustness"])
    return {
        "weight_profile": name,
        "weights": w,
        "top_n": n,
        "scored": len(rows),
        "confidence": summary["confidence"],
        "uninformative": summary["uninformative"],
        "method": summary["method"],
        "note": "Sub-scores are percentile ranks (0-100) among this run's feasible candidates.",
        "sites": [
            {
                "site_id": str(rows[r.index][1].id),
                "name": rows[r.index][1].name,
                "host_type": rows[r.index][1].host_type,
                "origin": rows[r.index][1].origin,
                "rank": r.rank,
                "score_total": r.total,
                "robustness": r.robustness,
                "sub_scores": rows[r.index][0].sub_scores,
            }
            for r in ranked[:limit]
        ],
    }


@router.get("/runs/{run_id}/rejected")
async def run_rejected(
    run_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> list[dict[str, Any]]:
    await _get_run(session, run_id)
    rows = (
        await session.execute(
            select(CandidateSite, FeasibilityResult)
            .join(FeasibilityResult, FeasibilityResult.site_id == CandidateSite.id)
            .where(CandidateSite.created_by_run == run_id, CandidateSite.status == "rejected")
            .order_by(CandidateSite.origin, CandidateSite.name)
        )
    ).all()
    return [
        {
            "site_id": str(site.id),
            "name": site.name,
            "origin": site.origin,
            "host_type": site.host_type,
            "reason_codes": result.reason_codes,
            "reasons": {code: result.details[code] for code in result.reason_codes},
        }
        for site, result in rows
    ]

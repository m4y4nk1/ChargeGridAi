"""Optimisation results (Section 9.7, modules C and D): /runs/{id}/optimisations,
/optimisations/{id}, /optimisations/{id}/cells, POST /runs/{id}/whatif."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any, Literal

import h3
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Principal, audit, get_principal
from app.db.models.planning import (
    CandidateSite,
    Optimisation,
    OptimisationSite,
    PlanningRun,
    RunSite,
)
from app.db.session import get_session
from app.providers.base import Mode, Point
from app.providers.valhalla.routing import ValhallaRoutingProvider
from app.services.optimisation_run import OptimisationError, whatif
from app.services.site_economics import EconomicsError, site_economics
from app.services.site_energy import EnergyError, site_energy

router = APIRouter(tags=["optimisation"])

# Drive-time bands for the Voronoi view (Valhalla allows at most four contours).
VORONOI_BANDS = (5, 10, 20, 30)


def _summary(o: Optimisation) -> dict[str, Any]:
    return {
        "id": str(o.id),
        "run_id": str(o.run_id),
        "kind": o.kind,
        "strategy": o.strategy,
        "label": o.params.get("label") or ("Scenario plan" if o.kind == "base" else o.kind),
        "params": o.params,
        "status": o.status,
        "solver_stats": o.solver_stats,
        "kpis": o.kpis,
        "on_front": o.on_front,
        "diff": o.diff,
        "created_at": o.created_at,
    }


async def _get(session: AsyncSession, opt_id: uuid.UUID) -> Optimisation:
    opt = await session.get(Optimisation, opt_id)
    if opt is None:
        raise HTTPException(404, "Optimisation not found")
    return opt


async def _with_sites(session: AsyncSession, opt: Optimisation) -> dict[str, Any]:
    rows = (
        await session.execute(
            select(
                OptimisationSite,
                CandidateSite.name,
                CandidateSite.host_type,
                CandidateSite.origin,
                func.ST_Y(CandidateSite.geom),
                func.ST_X(CandidateSite.geom),
            )
            .join(CandidateSite, CandidateSite.id == OptimisationSite.site_id)
            .where(OptimisationSite.optimisation_id == opt.id)
            .order_by(OptimisationSite.served_kwh.desc())
        )
    ).all()
    return {
        **_summary(opt),
        "label_note": "Candidate sites — require field verification",
        "sites": [
            {
                "site_id": str(s.site_id),
                "name": name,
                "host_type": host,
                "origin": origin,
                "lat": lat,
                "lng": lng,
                "bundle": s.bundle,
                "charge_points": s.charge_points,
                "cost_inr": s.cost_inr,
                "capacity_kwh": s.capacity_kwh,
                "served_kwh": s.served_kwh,
                "served_population": s.served_population,
                "utilisation": round(s.served_kwh / s.capacity_kwh, 3) if s.capacity_kwh else None,
                **s.detail,
            }
            for s, name, host, origin, lat, lng in rows
        ],
    }


@router.get("/runs/{run_id}/optimisations")
async def list_optimisations(
    run_id: uuid.UUID,
    kind: Literal["base", "whatif", "strategy", "pareto", "phased"] | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    if await session.get(PlanningRun, run_id) is None:
        raise HTTPException(404, "Run not found")
    query = select(Optimisation).where(Optimisation.run_id == run_id)
    if kind:
        query = query.where(Optimisation.kind == kind)
    rows = (await session.execute(query.order_by(Optimisation.created_at))).scalars()
    return [_summary(o) for o in rows]


@router.get("/optimisations/{opt_id}")
async def get_optimisation(
    opt_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    return await _with_sites(session, await _get(session, opt_id))


class WhatIfIn(BaseModel):
    budget_inr: int | None = Field(default=None, gt=0)
    max_sites: int | None = Field(default=None, ge=1, le=500)
    adoption_multiplier: float | None = Field(default=None, gt=0, le=5)


@router.post("/runs/{run_id}/whatif")
async def run_whatif(
    run_id: uuid.UUID,
    body: WhatIfIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """Re-optimise the run's plan with a new budget, site limit or adoption multiplier
    and diff it against the base plan. Reuses the run's scores and catchments."""
    try:
        opt = await whatif(
            session, run_id, body.budget_inr, body.max_sites, body.adoption_multiplier
        )
    except OptimisationError as exc:
        raise HTTPException(422, str(exc)) from exc
    await audit(
        session,
        principal,
        "run.whatif",
        "optimisation",
        str(opt.id),
        body.model_dump(exclude_none=True),
        request,
    )
    await session.commit()
    return await _with_sites(session, opt)


@router.get("/optimisations/{opt_id}/sites/{site_id}/energy/google")
async def site_energy_google(
    opt_id: uuid.UUID, site_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    """Google Solar result for a site: RESTRICTED_GOOGLE content, for display on the
    Google Maps canvas only (ADR 0002, 0008). Never cached beyond 30 days, never exported."""
    await _get(session, opt_id)
    try:
        energy = await site_energy(session, opt_id, site_id)
    except (EconomicsError, EnergyError) as exc:
        raise HTTPException(422, str(exc)) from exc
    site = (
        await session.execute(
            select(
                CandidateSite.name, func.ST_Y(CandidateSite.geom), func.ST_X(CandidateSite.geom)
            ).where(CandidateSite.id == site_id)
        )
    ).first()
    if site is None:
        raise HTTPException(404, "Site not found")
    return {
        "site": {"id": str(site_id), "name": site[0], "lat": site[1], "lng": site[2]},
        "license_class": "RESTRICTED_GOOGLE",
        "display": "google_canvas_only",
        "attribution": "Solar data © Google",
        "google": energy.get("google"),
        "google_note": energy.get("google_note"),
        "open_recommended": energy["open"]["recommended"],
    }


class Period(BaseModel):
    year: int = Field(ge=2026, le=2035)
    budget_inr: int = Field(ge=0)


class PhasingIn(BaseModel):
    periods: list[Period] | None = Field(
        default=None, description="Default: 2026/2028/2030 with the plan's budget split evenly"
    )
    max_sites: int | None = Field(default=None, ge=1, le=500)
    discount_rate: float = Field(default=0.12, ge=0, le=0.5)


@router.post("/runs/{run_id}/phasing")
async def run_phasing(
    run_id: uuid.UUID,
    body: PhasingIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """Model C: when to build each site across periods (sites stay open, may expand)."""
    from app.services.phasing import DEFAULT_PERIODS, phase_run

    periods = [p.model_dump() for p in body.periods] if body.periods else None
    if periods is None:
        base = (
            await session.execute(
                select(Optimisation).where(
                    Optimisation.run_id == run_id, Optimisation.kind == "base"
                )
            )
        ).scalar_one_or_none()
        if base is None:
            raise HTTPException(409, "No base plan; give periods with budgets")
        share = float(base.params["budget_inr"]) / len(DEFAULT_PERIODS)
        periods = [{"year": y, "budget_inr": share} for y in DEFAULT_PERIODS]
    try:
        opt = await phase_run(session, run_id, periods, body.max_sites, body.discount_rate)
    except OptimisationError as exc:
        raise HTTPException(422, str(exc)) from exc
    await audit(
        session,
        principal,
        "run.phasing",
        "optimisation",
        str(opt.id),
        {"periods": periods},
        request,
    )
    await session.commit()
    return await _with_sites(session, opt)


# --- module D: which station serves each cell ---------------------------------------------


def _banded_voronoi(
    sites: Sequence[tuple[str, float, float]], bands: dict[str, list[list[str]]]
) -> dict[str, dict[str, Any]]:
    """Assign each cell to the site it reaches in the shortest drive-time band; ties go to
    the nearer site in a straight line. `bands[site][b]` = cells within VORONOI_BANDS[b]."""
    best: dict[str, tuple[int, float, str]] = {}
    for site_id, lat, lng in sites:
        for b, cells in enumerate(bands[site_id]):
            for cell in cells:
                clat, clng = h3.cell_to_latlng(cell)
                d = (clat - lat) ** 2 + (clng - lng) ** 2
                key = (b, d, site_id)
                if cell not in best or key < best[cell]:
                    best[cell] = key
    return {
        cell: {"site_id": site_id, "minutes": VORONOI_BANDS[b]}
        for cell, (b, _, site_id) in best.items()
    }


async def _voronoi_bands(lat: float, lng: float) -> list[list[str]]:
    from app.services.scoring_run import _cells_in, _isochrone_shape

    router_ = ValhallaRoutingProvider()
    polygons = await router_.isochrone(Point(lat=lat, lng=lng), list(VORONOI_BANDS), Mode.DRIVE)
    # Valhalla returns contours largest first; pair them with the sorted bands.
    shapes = [_isochrone_shape([p]) for p in polygons]
    areas = sorted(((s.area if s is not None else 0.0), i) for i, s in enumerate(shapes))
    ordered = [shapes[i] for _, i in areas]
    return [_cells_in(s, lat, lng) if s is not None else [] for s in ordered]


@router.get("/optimisations/{opt_id}/cells")
async def optimisation_cells(
    opt_id: uuid.UUID,
    mode: Literal["assignment", "voronoi"] = "assignment",
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Module D. `assignment`: the site serving most of each cell's demand in the plan.
    `voronoi`: the site each cell reaches soonest by car (banded drive time)."""
    opt = await _get(session, opt_id)
    if mode == "assignment":
        if opt.cell_assignment is None:
            raise HTTPException(409, "No cell assignment stored for this optimisation")
        return {"mode": mode, "cells": opt.cell_assignment}
    if opt.drive_time_voronoi is None:
        rows = (
            await session.execute(
                select(
                    OptimisationSite.site_id,
                    func.ST_Y(CandidateSite.geom),
                    func.ST_X(CandidateSite.geom),
                )
                .join(CandidateSite, CandidateSite.id == OptimisationSite.site_id)
                .where(OptimisationSite.optimisation_id == opt.id)
            )
        ).all()
        sites = [(str(s), float(lat), float(lng)) for s, lat, lng in rows]
        bands = {s: await _voronoi_bands(lat, lng) for s, lat, lng in sites}
        opt.drive_time_voronoi = {
            "bands_minutes": list(VORONOI_BANDS),
            "cells": _banded_voronoi(sites, bands),
        }
        await session.commit()
    return {"mode": mode, **opt.drive_time_voronoi}


@router.get("/runs/{run_id}/why-not")
async def run_why_not(
    run_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> list[dict[str, Any]]:
    """Strong candidates the base plan left out, each re-solved with the site forced in
    (Section 9.7): what it would cost the plan, which site it would displace, and the
    sub-score that decided it."""
    rows = (
        await session.execute(
            select(RunSite, CandidateSite.name, CandidateSite.host_type)
            .join(CandidateSite, CandidateSite.id == RunSite.site_id)
            .where(RunSite.run_id == run_id, RunSite.why_not.is_not(None))
            .order_by(RunSite.rank)
        )
    ).all()
    return [
        {
            "site_id": str(rs.site_id),
            "name": name,
            "host_type": host,
            "rank": rs.rank,
            "score_total": rs.score_total,
            "why_not": rs.why_not,
        }
        for rs, name, host in rows
    ]


# --- sizing and finance (Phase 7) ----------------------------------------------------------


@router.get("/optimisations/{opt_id}/sites/{site_id}/economics")
async def site_economics_view(
    opt_id: uuid.UUID,
    site_id: uuid.UUID,
    subsidy: bool = False,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Charger sizing (Erlang-C + SimPy) and finance (P10/P50/P90, cases, tornado) for a
    site in a plan. Computed on first request (a few seconds) and cached. `subsidy`
    applies the configured scheme share to upstream grid costs; it is never assumed."""
    await _get(session, opt_id)
    try:
        config = await site_economics(session, opt_id, site_id, subsidy)
    except EconomicsError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {
        "optimisation_id": str(opt_id),
        "site_id": str(site_id),
        "label": "Candidate site — requires field verification",
        "sizing": config.sizing,
        "finance": config.finance,
        "placeholders": config.placeholders,
    }


@router.get("/optimisations/{opt_id}/sites/{site_id}/energy")
async def site_energy_view(
    opt_id: uuid.UUID, site_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    """Solar + battery recommendation (Section 9.9) and its effect on the grid connection
    and OPEX. `open` uses NASA POWER and a canopy; `google` (RESTRICTED_GOOGLE, only
    with a key and coverage) uses Google Solar and is withheld here: the open map's
    pages can't show Google content, so only its availability is reported."""
    await _get(session, opt_id)
    try:
        energy = await site_energy(session, opt_id, site_id)
    except (EconomicsError, EnergyError) as exc:
        raise HTTPException(422, str(exc)) from exc
    google = energy.get("google")
    return {
        "optimisation_id": str(opt_id),
        "site_id": str(site_id),
        **{k: v for k, v in energy.items() if k != "google"},
        "google_available": google is not None,
        "google_display": "Open the site in Google map mode to see the Google Solar result"
        if google
        else None,
    }

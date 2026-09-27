"""Digital-twin scenarios (Phase 12): POST /twin/runs, GET /twin/runs[/{id}]."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Principal, audit, get_principal
from app.db.models.twin import TwinRun
from app.db.session import get_session
from app.services.twin import TwinError, run_twin, save_twin

router = APIRouter(prefix="/twin", tags=["twin"])


class TwinIn(BaseModel):
    kind: Literal["add_chargers", "adoption", "grid_constraints"]
    n: int = Field(default=10_000, ge=1, le=1_000_000)
    charger_class: str = "DC_60"
    multiplier: float = Field(default=1.3, ge=0.1, le=5)
    year: int = Field(default=2030, ge=2026, le=2035)
    case: Literal["slow", "base", "fast"] = "base"
    regions: list[str] | None = None
    max_per_cell: int = Field(default=20, ge=1, le=200)


def _out(r: TwinRun) -> dict[str, Any]:
    return {
        "id": str(r.id),
        "kind": r.kind,
        "params": r.params,
        "result": r.result,
        "created_at": r.created_at,
    }


@router.post("/runs", status_code=201)
async def create_twin(
    body: TwinIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    params = body.model_dump(exclude={"kind"})
    try:
        result = await run_twin(session, body.kind, params)
    except TwinError as exc:
        raise HTTPException(422, str(exc)) from exc
    run_id = await save_twin(session, body.kind, params, result, principal.org_id)
    await audit(
        session, principal, "twin.run", "twin_run", str(run_id), {"kind": body.kind}, request
    )
    await session.commit()
    run = await session.get(TwinRun, run_id)
    assert run is not None
    return _out(run)


@router.get("/runs")
async def list_twin(
    session: AsyncSession = Depends(get_session), principal: Principal = Depends(get_principal)
) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(TwinRun)
            .where(TwinRun.org_id == principal.org_id)
            .order_by(TwinRun.created_at.desc())
            .limit(30)
        )
    ).scalars()
    return [_out(r) for r in rows]


@router.get("/runs/{twin_id}")
async def get_twin(
    twin_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    run = await session.get(TwinRun, twin_id)
    if run is None or run.org_id != principal.org_id:
        raise HTTPException(404, "Not found")
    return _out(run)

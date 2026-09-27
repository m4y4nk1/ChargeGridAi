"""Grid Impact (module E): GET /grid/impact?run_id= and DISCOM coverage."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.grid import GridAssetRated
from app.db.session import get_session
from app.services.grid_impact import GridImpactError, grid_impact

router = APIRouter(prefix="/grid", tags=["grid"])


@router.get("/impact")
async def get_grid_impact(
    run_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    """Load curves, charger profile and grid asset utilisation for a run's plan, with
    confidence. Proximity-only until DISCOM ratings and loadings are ingested."""
    try:
        return await grid_impact(session, run_id)
    except GridImpactError as exc:
        raise HTTPException(404 if "not found" in str(exc) else 409, str(exc)) from exc


@router.get("/coverage")
async def get_coverage(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """Which DISCOMs have supplied asset data (counts only; assets stay internal)."""
    rows = (
        await session.execute(
            select(
                GridAssetRated.discom,
                GridAssetRated.asset_type,
                func.count(),
                func.max(GridAssetRated.measured_on),
            ).group_by(GridAssetRated.discom, GridAssetRated.asset_type)
        )
    ).all()
    return {
        "discoms": [
            {"discom": d, "asset_type": t, "assets": n, "latest_measurement": str(m) if m else None}
            for d, t, n, m in rows
        ],
        "data_backed": bool(rows),
    }

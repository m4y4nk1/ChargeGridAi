"""GET /readiness: data readiness score per registered region (Phase 12)."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.regions import get_region
from app.db.session import get_session
from app.services.readiness import all_readiness, region_readiness

router = APIRouter(prefix="/readiness", tags=["readiness"])


@router.get("")
async def list_readiness(session: AsyncSession = Depends(get_session)) -> list[dict[str, Any]]:
    return await all_readiness(session)


@router.get("/{region_id}")
async def one_readiness(
    region_id: str, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    try:
        region = get_region(region_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    return await region_readiness(session, region)

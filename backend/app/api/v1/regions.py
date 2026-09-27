from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.regions import get_region, list_regions
from app.db.session import get_session
from app.schemas.region import Region, RegionSummary

router = APIRouter(prefix="/regions", tags=["regions"])


@router.get("", response_model=list[RegionSummary])
async def get_regions(session: AsyncSession = Depends(get_session)) -> list[RegionSummary]:
    return await list_regions(session)


@router.get("/{region_id}", response_model=Region)
async def get_region_detail(region_id: int, session: AsyncSession = Depends(get_session)) -> Region:
    region = await get_region(session, region_id)
    if region is None:
        raise HTTPException(status_code=404, detail="Region not found")
    return region

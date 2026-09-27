import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.stations import station_detail, station_stats, stations_geojson
from app.db.session import get_session
from app.schemas.station import StationDetail, StationStats

router = APIRouter(prefix="/stations", tags=["stations"])


def _parse_bbox(bbox: str | None) -> tuple[float, float, float, float] | None:
    if bbox is None:
        return None
    try:
        min_lng, min_lat, max_lng, max_lat = (float(v) for v in bbox.split(","))
    except ValueError as exc:
        raise HTTPException(422, "bbox must be min_lng,min_lat,max_lng,max_lat") from exc
    return (min_lng, min_lat, max_lng, max_lat)


@router.get("")
async def list_stations(
    bbox: str | None = Query(None, description="min_lng,min_lat,max_lng,max_lat"),
    min_kw: float | None = None,
    operator: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Fused stations as GeoJSON, each with a provenance summary."""
    return await stations_geojson(session, _parse_bbox(bbox), min_kw, operator)


@router.get("/stats", response_model=StationStats)
async def get_station_stats(
    region_id: int | None = None, session: AsyncSession = Depends(get_session)
) -> StationStats:
    return await station_stats(session, region_id)


@router.get("/{station_id}", response_model=StationDetail)
async def get_station(
    station_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> StationDetail:
    detail = await station_detail(session, station_id)
    if detail is None:
        raise HTTPException(404, "Station not found")
    return detail

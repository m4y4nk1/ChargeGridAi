"""Live Google content for the Google map canvas (ADR 0002, 0014).

Google Maps Platform terms allow storing place IDs only, so these results are fetched
on request, returned with their licence and attribution, and never written to the
database (the cost guard records the call, not the content). The web app shows them
only on the Google map, never on the open MapLibre map or in exports.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import get_settings
from app.db.repositories.api_usage import cost_guard_for
from app.db.session import get_session
from app.providers.base import POICategory, Point
from app.providers.google.errors import GoogleAPIError
from app.providers.google.places import CATEGORY_TO_GOOGLE_TYPES, GooglePlacesProvider

router = APIRouter(prefix="/google", tags=["google"])

DEFAULT_CATEGORIES = [
    POICategory.FUEL,
    POICategory.PARKING,
    POICategory.MALL,
    POICategory.RESTAURANT,
    POICategory.HOTEL,
    POICategory.SUPERMARKET,
    POICategory.TRANSIT_HUB,
    POICategory.EV_CHARGER,
]


@router.get("/config")
async def google_config() -> dict[str, Any]:
    s = get_settings()
    return {
        "places": bool(s.google_maps_server_key),
        "solar": s.google_solar_enabled and bool(s.google_maps_server_key),
    }


@router.get("/places/nearby")
async def places_nearby(
    lat: float = Query(ge=-90, le=90),
    lng: float = Query(ge=-180, le=180),
    radius_m: int = Query(500, ge=50, le=2000),
    categories: str | None = Query(None, description="Comma-separated, e.g. FUEL,MALL"),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    key = get_settings().google_maps_server_key
    if not key:
        raise HTTPException(503, "Google Places isn't configured (GOOGLE_MAPS_SERVER_KEY)")
    try:
        cats = (
            [POICategory(c.strip().upper()) for c in categories.split(",") if c.strip()]
            if categories
            else DEFAULT_CATEGORIES
        )
    except ValueError as exc:
        raise HTTPException(
            422, f"categories must be among {[c.value for c in CATEGORY_TO_GOOGLE_TYPES]}"
        ) from exc
    provider = GooglePlacesProvider(key, cost_guard_for(session))
    try:
        pois = await provider.search_nearby(Point(lat=lat, lng=lng), radius_m, cats)
    except GoogleAPIError as exc:
        raise HTTPException(502, str(exc)) from exc
    except Exception as exc:  # the cost guard refuses (cap reached, price missing)
        raise HTTPException(429, f"Google Places call refused: {exc}") from exc
    await session.commit()  # the usage record, not the results
    return {
        "license_class": "RESTRICTED_GOOGLE",
        "display": "google_canvas_only",
        "attribution": "Places data © Google",
        "stored": False,
        "places": [
            {
                "place_id": p.id,
                "name": p.name,
                "category": p.category,
                "lat": p.location.lat,
                "lng": p.location.lng,
            }
            for p in pois
        ],
    }

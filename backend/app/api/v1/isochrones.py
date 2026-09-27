from fastapi import APIRouter, Query

from app.providers.base import Mode, Point
from app.providers.valhalla.routing import ValhallaRoutingProvider
from app.schemas.isochrone import IsochroneFeatureCollection

router = APIRouter(tags=["isochrones"])

_provider = ValhallaRoutingProvider()


@router.get("/isochrones", response_model=IsochroneFeatureCollection)
async def get_isochrones(
    lat: float,
    lng: float,
    minutes: str = Query("5,10,15", description="Comma-separated drive-time minutes"),
) -> IsochroneFeatureCollection:
    minute_values = [int(m) for m in minutes.split(",") if m.strip()]
    polygons = await _provider.isochrone(Point(lat=lat, lng=lng), minute_values, Mode.DRIVE)

    features = [
        {
            "type": "Feature",
            "properties": {"minutes": minute},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[list(pt) for pt in ring] for ring in polygon.coordinates],
            },
        }
        for minute, polygon in zip(sorted(minute_values), polygons, strict=False)
    ]
    return IsochroneFeatureCollection(features=features)

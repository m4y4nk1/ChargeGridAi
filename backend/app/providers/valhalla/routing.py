"""Valhalla-backed RoutingProvider (Section 7.2 / 9.1 of the project brief).

Valhalla is our self-hosted routing engine over the OSM extract — the
default for isochrones and bulk matrices. It carries no license
restriction of its own (OSM-derived, self-hosted).
"""

from __future__ import annotations

from datetime import datetime

import httpx

from app.core.settings import get_settings
from app.providers.base import LicenseClass, Mode, Point, Polygon, Route, TimeDistanceMatrix

_MODE_TO_COSTING = {
    Mode.DRIVE: "auto",
    Mode.WALK: "pedestrian",
}


class ValhallaRoutingProvider:
    name = "valhalla"
    license_class = LicenseClass.OPEN

    def __init__(self, base_url: str | None = None) -> None:
        self._base_url = base_url or get_settings().valhalla_url

    async def isochrone(
        self, center: Point, minutes: list[int], mode: Mode = Mode.DRIVE
    ) -> list[Polygon]:
        payload = {
            "locations": [{"lat": center.lat, "lon": center.lng}],
            "costing": _MODE_TO_COSTING[mode],
            "contours": [{"time": m} for m in sorted(minutes)],
            "polygons": True,
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(f"{self._base_url}/isochrone", json=payload)
            response.raise_for_status()
            data = response.json()

        polygons: list[Polygon] = []
        for feature in data.get("features", []):
            geometry = feature["geometry"]
            if geometry["type"] == "Polygon":
                rings = geometry["coordinates"]
            elif geometry["type"] == "MultiPolygon":
                rings = [ring for polygon in geometry["coordinates"] for ring in polygon]
            else:
                continue
            polygons.append(Polygon(coordinates=[[tuple(pt) for pt in ring] for ring in rings]))
        return polygons

    async def route(
        self, o: Point, d: Point, depart_at: datetime | None = None, traffic: bool = False
    ) -> Route:
        payload = {
            "locations": [{"lat": o.lat, "lon": o.lng}, {"lat": d.lat, "lon": d.lng}],
            "costing": "auto",
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(f"{self._base_url}/route", json=payload)
            response.raise_for_status()
            data = response.json()

        leg = data["trip"]["legs"][0]
        return Route(distance_m=leg["summary"]["length"] * 1000, duration_s=leg["summary"]["time"])

    async def route_line(
        self, points: list[Point]
    ) -> tuple[float, float, list[tuple[float, float]]]:
        """Driving route through `points`: (distance m, duration s, [(lng, lat), ...])."""
        payload = {
            "locations": [{"lat": p.lat, "lon": p.lng} for p in points],
            "costing": "auto",
        }
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(f"{self._base_url}/route", json=payload)
            response.raise_for_status()
            data = response.json()
        coords: list[tuple[float, float]] = []
        for leg in data["trip"]["legs"]:
            coords.extend(decode_polyline6(leg["shape"]))
        summary = data["trip"]["summary"]
        return summary["length"] * 1000, summary["time"], coords

    async def matrix(
        self,
        origins: list[Point],
        dests: list[Point],
        depart_at: datetime | None = None,
    ) -> TimeDistanceMatrix:
        payload = {
            "sources": [{"lat": p.lat, "lon": p.lng} for p in origins],
            "targets": [{"lat": p.lat, "lon": p.lng} for p in dests],
            "costing": "auto",
        }
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(f"{self._base_url}/sources_to_targets", json=payload)
            response.raise_for_status()
            data = response.json()

        rows = data["sources_to_targets"]
        durations = [[cell["time"] for cell in row] for row in rows]
        distances = [[cell["distance"] * 1000 for cell in row] for row in rows]
        return TimeDistanceMatrix(
            origins=origins, destinations=dests, durations_s=durations, distances_m=distances
        )


def decode_polyline6(encoded: str) -> list[tuple[float, float]]:
    """Valhalla's encoded shape (polyline, 6 decimals) -> [(lng, lat), ...]."""
    coords, index, lat, lng = [], 0, 0, 0
    while index < len(encoded):
        deltas = []
        for _ in range(2):
            shift = result = 0
            while True:
                b = ord(encoded[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            deltas.append(~(result >> 1) if result & 1 else result >> 1)
        lat += deltas[0]
        lng += deltas[1]
        coords.append((lng / 1e6, lat / 1e6))
    return coords

"""Google Geocoding adapter (Section 7.1 row 10) for user-entered site addresses.

Only `place_id` may be persisted; coordinates may be cached up to 30 days
(docs/verification.md item 1). Cost-guarded like every paid call.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from app.core.cost_guard import CostGuard
from app.providers.base import LicenseClass, Point
from app.providers.google.errors import raise_for_google
from app.providers.google.places import GoogleNotConfigured

GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"


@dataclass(frozen=True)
class GeocodeResult:
    place_id: str
    location: Point
    formatted_address: str
    retrieved_at: datetime
    license_class: LicenseClass = LicenseClass.RESTRICTED_GOOGLE


class GoogleGeocoder:
    name = "google_geocoding"

    def __init__(
        self, api_key: str, cost_guard: CostGuard, client: httpx.AsyncClient | None = None
    ):
        if not api_key:
            raise GoogleNotConfigured("GOOGLE_MAPS_SERVER_KEY is not set")
        self._key = api_key
        self._guard = cost_guard
        self._client = client or httpx.AsyncClient(timeout=30.0)

    async def geocode(self, address: str) -> GeocodeResult | None:
        await self._guard.charge(self.name, "geocode")
        response = await self._client.get(
            GEOCODE_URL, params={"address": address, "region": "in", "key": self._key}
        )
        raise_for_google(response, "Google Geocoding")
        results = response.json().get("results") or []
        if not results:
            return None
        top = results[0]
        loc = top["geometry"]["location"]
        return GeocodeResult(
            place_id=top["place_id"],
            location=Point(lat=loc["lat"], lng=loc["lng"]),
            formatted_address=top.get("formatted_address", ""),
            retrieved_at=datetime.now(UTC),
        )

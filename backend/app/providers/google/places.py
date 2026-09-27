"""Google Places API (New) POIProvider (Section 7.1 row 8, Section 7.2).

Governance (docs/verification.md items 1-2):
- Always sends X-Goog-FieldMask; masks are per use case so only the SKUs
  actually needed are billed.
- Every call reserves its cost with the CostGuard first.
- Results are RESTRICTED_GOOGLE: only `place_id` may be persisted; the rest
  is for live display on the Google canvas only (see licenseGuard.ts).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.cost_guard import CostGuard
from app.providers.base import POI, LicenseClass, POICategory, Point, Polygon
from app.providers.google.errors import raise_for_google

BASE_URL = "https://places.googleapis.com/v1"

FIELD_MASK_POI_BASIC = "places.id,places.displayName,places.location,places.types"
FIELD_MASK_EV_DETAIL = FIELD_MASK_POI_BASIC + ",places.evChargeOptions"

# Our categories -> Places (New) primary types (Section 7.2 mapping table).
CATEGORY_TO_GOOGLE_TYPES: dict[POICategory, list[str]] = {
    POICategory.EV_CHARGER: ["electric_vehicle_charging_station"],
    POICategory.FUEL: ["gas_station"],
    POICategory.PARKING: ["parking"],
    POICategory.MALL: ["shopping_mall"],
    POICategory.HOTEL: ["lodging"],
    POICategory.RESTAURANT: ["restaurant"],
    POICategory.SUPERMARKET: ["supermarket"],
    POICategory.HOSPITAL: ["hospital"],
    POICategory.TRANSIT_HUB: ["bus_station", "train_station", "transit_station"],
}
_TYPE_TO_CATEGORY = {t: c for c, types in CATEGORY_TO_GOOGLE_TYPES.items() for t in types}


class GoogleNotConfigured(RuntimeError):
    pass


def _to_poi(place: dict[str, Any], retrieved_at: datetime) -> POI:
    types = place.get("types") or []
    category = next((_TYPE_TO_CATEGORY[t] for t in types if t in _TYPE_TO_CATEGORY), None)
    location = place["location"]
    return POI(
        id=place["id"],
        category=(category or "OTHER"),
        name=(place.get("displayName") or {}).get("text", ""),
        location=Point(lat=location["latitude"], lng=location["longitude"]),
        source="google_places",
        license_class=LicenseClass.RESTRICTED_GOOGLE,
        retrieved_at=retrieved_at,
        confidence="MEDIUM",
    )


class GooglePlacesProvider:
    name = "google_places"
    license_class = LicenseClass.RESTRICTED_GOOGLE

    def __init__(
        self, api_key: str, cost_guard: CostGuard, client: httpx.AsyncClient | None = None
    ):
        if not api_key:
            raise GoogleNotConfigured("GOOGLE_MAPS_SERVER_KEY is not set")
        self._key = api_key
        self._guard = cost_guard
        self._client = client or httpx.AsyncClient(timeout=30.0)

    async def _post(self, path: str, body: dict[str, Any], field_mask: str, sku: str) -> list[POI]:
        await self._guard.charge(self.name, sku)
        response = await self._client.post(
            f"{BASE_URL}{path}",
            json=body,
            headers={"X-Goog-Api-Key": self._key, "X-Goog-FieldMask": field_mask},
        )
        raise_for_google(response, "Google Places")
        retrieved_at = datetime.now(UTC)
        return [_to_poi(p, retrieved_at) for p in response.json().get("places", [])]

    async def search_nearby(
        self, center: Point, radius_m: int, categories: list[POICategory]
    ) -> list[POI]:
        types = [t for c in categories for t in CATEGORY_TO_GOOGLE_TYPES.get(c, [])]
        body = {
            "includedTypes": types,
            "maxResultCount": 20,
            "locationRestriction": {
                "circle": {
                    "center": {"latitude": center.lat, "longitude": center.lng},
                    "radius": float(radius_m),
                }
            },
        }
        mask = (
            FIELD_MASK_EV_DETAIL if categories == [POICategory.EV_CHARGER] else FIELD_MASK_POI_BASIC
        )
        return await self._post("/places:searchNearby", body, mask, "search_nearby")

    async def search_text(self, query: str, bias: Polygon | None) -> list[POI]:
        body: dict[str, Any] = {"textQuery": query}
        if bias is not None:
            ring = bias.coordinates[0]
            lats, lngs = [p[1] for p in ring], [p[0] for p in ring]
            body["locationBias"] = {
                "rectangle": {
                    "low": {"latitude": min(lats), "longitude": min(lngs)},
                    "high": {"latitude": max(lats), "longitude": max(lngs)},
                }
            }
        return await self._post("/places:searchText", body, FIELD_MASK_POI_BASIC, "search_text")

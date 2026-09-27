"""Google Solar API adapter (Section 9.9): `buildingInsights:findClosest` for a site.

Returns the roof's maximum PV array and its modelled yearly DC energy, which the
energy engine uses as the PV capacity limit and to scale the hourly profile. Solar
API returns NOT_FOUND where it has no coverage (docs/verification.md item 3); that
is a normal outcome and the engine falls back to NASA POWER plus a canopy estimate.

Licence: RESTRICTED_GOOGLE. Results may be cached for at most 30 days, are never
written to the raw data lake, and are not shown beside the open (MapLibre) map.
Every call goes through the cost guard, which refuses calls until a key, a monthly
cap and the INR/USD rate are configured. `dataLayers` ($30 / 1,000) is never called.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.core.cost_guard import CostGuard
from app.providers.base import LicenseClass
from app.providers.google.errors import raise_for_google
from app.providers.google.places import GoogleNotConfigured

URL = "https://solar.googleapis.com/v1/buildingInsights:findClosest"
CACHE_DAYS = 30
# findClosest can return a building some way off; beyond this it isn't the site's host.
MAX_BUILDING_DISTANCE_M = 150.0


@dataclass(frozen=True)
class SolarInsight:
    building_name: str
    building_distance_m: float
    imagery_quality: str
    max_panels: int
    panel_capacity_w: float
    max_array_kwp: float
    max_array_area_m2: float
    yearly_dc_kwh_at_max: float  # modelled DC energy of the largest array
    max_sunshine_hours_per_year: float
    retrieved_at: datetime
    expires_at: datetime
    license_class: LicenseClass = LicenseClass.RESTRICTED_GOOGLE

    @property
    def specific_yield_kwh_per_kwp(self) -> float:
        return self.yearly_dc_kwh_at_max / self.max_array_kwp if self.max_array_kwp else 0.0


def _distance_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(h))


def parse_building_insights(body: dict[str, Any], lat: float, lng: float) -> SolarInsight | None:
    """None when the building has no usable array or is too far from the site."""
    sp = body.get("solarPotential") or {}
    configs = sp.get("solarPanelConfigs") or []
    if not configs or not sp.get("maxArrayPanelsCount"):
        return None
    centre = body.get("center") or {}
    distance = _distance_m(lat, lng, centre.get("latitude", lat), centre.get("longitude", lng))
    if distance > MAX_BUILDING_DISTANCE_M:
        return None
    largest = max(configs, key=lambda c: c.get("panelsCount", 0))
    capacity_w = float(sp.get("panelCapacityWatts", 0) or 0)
    now = datetime.now(UTC)
    return SolarInsight(
        building_name=body.get("name", ""),
        building_distance_m=round(distance, 1),
        imagery_quality=body.get("imageryQuality", "UNKNOWN"),
        max_panels=int(sp["maxArrayPanelsCount"]),
        panel_capacity_w=capacity_w,
        max_array_kwp=int(sp["maxArrayPanelsCount"]) * capacity_w / 1000,
        max_array_area_m2=float(sp.get("maxArrayAreaMeters2", 0) or 0),
        yearly_dc_kwh_at_max=float(largest.get("yearlyEnergyDcKwh", 0) or 0),
        max_sunshine_hours_per_year=float(sp.get("maxSunshineHoursPerYear", 0) or 0),
        retrieved_at=now,
        expires_at=now + timedelta(days=CACHE_DAYS),
    )


class GoogleSolar:
    name = "google_solar"

    def __init__(
        self, api_key: str, cost_guard: CostGuard, client: httpx.AsyncClient | None = None
    ):
        if not api_key:
            raise GoogleNotConfigured("GOOGLE_MAPS_SERVER_KEY is not set")
        self._key = api_key
        self._guard = cost_guard
        self._client = client or httpx.AsyncClient(timeout=30.0)

    async def building_insights(self, lat: float, lng: float) -> SolarInsight | None:
        """None when Solar API has no coverage here (NOT_FOUND) or no usable roof."""
        await self._guard.charge(self.name, "building_insights")
        response = await self._client.get(
            URL,
            params={
                "location.latitude": lat,
                "location.longitude": lng,
                # LOW admits the widest coverage; quality is reported with the result.
                "requiredQuality": "LOW",
            },
            headers={"X-Goog-Api-Key": self._key},  # never in the URL (URLs get logged)
        )
        if response.status_code == 404:
            return None
        raise_for_google(response, "Google Solar")
        return parse_building_insights(response.json(), lat, lng)

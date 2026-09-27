"""Live Google Places: labelled RESTRICTED_GOOGLE, returned for the Google canvas only,
never stored (Google Maps Platform terms: place IDs only)."""

from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.settings import get_settings
from app.providers.base import POI, LicenseClass, Point


async def test_places_nearby_is_live_and_labelled(monkeypatch: Any) -> None:
    from app.api.v1 import google as google_api
    from app.main import app

    monkeypatch.setattr(get_settings(), "google_maps_server_key", "test-key")

    async def fake_search(self: Any, center: Point, radius_m: int, cats: list[Any]) -> list[POI]:
        assert radius_m == 300
        return [
            POI(
                id="ChIJ123",
                category="FUEL",
                name="Test Fuel",
                location=Point(lat=18.53, lng=73.85),
                source="google_places",
                license_class=LicenseClass.RESTRICTED_GOOGLE,
                retrieved_at=datetime.now(UTC),
                confidence="MEDIUM",
            )
        ]

    monkeypatch.setattr(google_api.GooglePlacesProvider, "search_nearby", fake_search)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t/api/v1"
    ) as api:
        r = await api.get(
            "/google/places/nearby", params={"lat": 18.53, "lng": 73.85, "radius_m": 300}
        )
        bad = await api.get(
            "/google/places/nearby", params={"lat": 18.53, "lng": 73.85, "categories": "NOPE"}
        )
    assert r.status_code == 200
    body = r.json()
    assert body["license_class"] == "RESTRICTED_GOOGLE" and body["stored"] is False
    assert body["display"] == "google_canvas_only" and "Google" in body["attribution"]
    assert body["places"] == [
        {"place_id": "ChIJ123", "name": "Test Fuel", "category": "FUEL", "lat": 18.53, "lng": 73.85}
    ]
    assert bad.status_code == 422


def test_solar_stays_on_nasa_unless_switched_on() -> None:
    assert get_settings().google_solar_enabled is False

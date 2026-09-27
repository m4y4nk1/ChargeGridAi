from app.providers.base import LicenseClass
from app.providers.google.solar import parse_building_insights

BODY = {
    "name": "buildings/ChIJ-example",
    "center": {"latitude": 18.53420, "longitude": 73.84770},
    "imageryQuality": "MEDIUM",
    "solarPotential": {
        "maxArrayPanelsCount": 400,
        "maxArrayAreaMeters2": 780.0,
        "maxSunshineHoursPerYear": 1700.0,
        "panelCapacityWatts": 400,
        "solarPanelConfigs": [
            {"panelsCount": 4, "yearlyEnergyDcKwh": 2400.0},
            {"panelsCount": 400, "yearlyEnergyDcKwh": 224000.0},
        ],
    },
}


def test_parses_the_largest_array_and_its_yield() -> None:
    s = parse_building_insights(BODY, 18.53418, 73.84765)
    assert s is not None
    assert s.max_array_kwp == 160.0
    assert s.yearly_dc_kwh_at_max == 224000.0
    assert s.specific_yield_kwh_per_kwp == 1400.0
    assert s.imagery_quality == "MEDIUM"
    assert s.building_distance_m < 10
    assert s.license_class is LicenseClass.RESTRICTED_GOOGLE
    assert (s.expires_at - s.retrieved_at).days == 30


def test_rejects_far_buildings_and_empty_roofs() -> None:
    assert parse_building_insights(BODY, 18.5400, 73.8477) is None  # ~630 m away
    assert (
        parse_building_insights({"center": BODY["center"], "solarPotential": {}}, 18.5342, 73.8477)
        is None
    )


def test_google_errors_never_carry_the_key() -> None:
    import httpx
    import pytest

    from app.providers.google.errors import GoogleAPIError, raise_for_google, redact

    key = "AIzaFAKEKEY123"
    request = httpx.Request("GET", f"https://solar.googleapis.com/v1/x?key={key}")
    response = httpx.Response(
        403,
        request=request,
        json={"error": {"status": "PERMISSION_DENIED", "message": "Solar API is disabled"}},
    )
    with pytest.raises(GoogleAPIError) as exc:
        raise_for_google(response, "Google Solar")
    assert key not in str(exc.value) and "PERMISSION_DENIED" in str(exc.value)
    assert redact(f"url?key={key}", key) == "url?key=***"

"""OCPI 2.2.1 client against a fake operator (spec-shaped responses)."""

import base64
import json
from typing import Any

import httpx
import pytest

from app.providers.base import ConnectorStandard, LicenseClass
from app.providers.ocpi.client import (
    OCPIClient,
    OCPIError,
    cdr_obs,
    connector_kw,
    location_to_station,
    session_obs,
)

BASE = "https://cpo.example/ocpi"
LOCATIONS = [
    {
        "id": "LOC1",
        "name": "Expressway Food Plaza",
        "address": "Km 55",
        "city": "Khalapur",
        "coordinates": {"latitude": "18.8012", "longitude": "73.2911"},
        "operator": {"name": "Acme"},
        "opening_times": {"twentyfourseven": True},
        "evses": [
            {
                "uid": "E1",
                "status": "AVAILABLE",
                "connectors": [
                    {
                        "id": "1",
                        "standard": "IEC_62196_T2_COMBO",
                        "power_type": "DC",
                        "max_voltage": 1000,
                        "max_amperage": 150,
                        "max_electric_power": 120000,
                    }
                ],
            },
            {"uid": "E2", "status": "REMOVED", "connectors": []},
        ],
    },
    {
        "id": "LOC2",
        "name": "Mall",
        "coordinates": {"latitude": "18.52", "longitude": "73.85"},
        "evses": [
            {
                "uid": "E3",
                "status": "CHARGING",
                "connectors": [
                    {
                        "id": "1",
                        "standard": "IEC_62196_T2",
                        "power_type": "AC_3_PHASE",
                        "max_voltage": 400,
                        "max_amperage": 32,
                    }
                ],
            }
        ],
    },
]


def _env(data: Any, status: int = 1000) -> dict[str, Any]:
    return {
        "data": data,
        "status_code": status,
        "status_message": "OK",
        "timestamp": "2026-09-27T00:00:00Z",
    }


def operator(seen: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        url = str(request.url)
        if url == f"{BASE}/versions":
            return httpx.Response(
                200,
                json=_env(
                    [
                        {"version": "2.1.1", "url": f"{BASE}/2.1.1"},
                        {"version": "2.2.1", "url": f"{BASE}/2.2.1"},
                    ]
                ),
            )
        if url == f"{BASE}/2.2.1":
            return httpx.Response(
                200,
                json=_env(
                    {
                        "version": "2.2.1",
                        "endpoints": [
                            {
                                "identifier": "locations",
                                "role": "SENDER",
                                "url": f"{BASE}/2.2.1/locations",
                            },
                            {"identifier": "cdrs", "role": "SENDER", "url": f"{BASE}/2.2.1/cdrs"},
                            {
                                "identifier": "tokens",
                                "role": "RECEIVER",
                                "url": f"{BASE}/2.2.1/tokens",
                            },
                        ],
                    }
                ),
            )
        if url.startswith(f"{BASE}/2.2.1/locations"):
            if "offset=1" in url:
                return httpx.Response(200, json=_env(LOCATIONS[1:]))
            return httpx.Response(
                200,
                json=_env(LOCATIONS[:1]),
                headers={
                    "Link": f'<{BASE}/2.2.1/locations?offset=1&limit=1>; rel="next"',
                    "X-Total-Count": "2",
                },
            )
        if url.startswith(f"{BASE}/2.2.1/cdrs"):
            return httpx.Response(
                200,
                json=_env(
                    [
                        {
                            "id": "C1",
                            "start_date_time": "2026-09-01T10:00:00Z",
                            "end_date_time": "2026-09-01T10:40:00Z",
                            "total_energy": 31.5,
                            "cdr_location": {"id": "LOC1", "evse_uid": "E1"},
                        },
                        {
                            "id": "C2",
                            "start_date_time": "2026-09-01T11:00:00Z",
                            "total_energy": 5,
                            "cdr_location": {},
                        },
                    ]
                ),
            )
        return httpx.Response(404, json=_env(None, 2000))

    return httpx.MockTransport(handler)


async def test_discovery_auth_pagination_and_mapping() -> None:
    seen: list[httpx.Request] = []
    client = OCPIClient(f"{BASE}/versions", "secret-token", transport=operator(seen))
    endpoints = await client.endpoints()
    assert set(endpoints) == {"locations", "cdrs"}  # RECEIVER endpoints ignored
    locations = await client.all("locations")
    assert [loc["id"] for loc in locations] == ["LOC1", "LOC2"]
    auth = seen[0].headers["Authorization"]
    assert auth == "Token " + base64.b64encode(b"secret-token").decode()
    assert "offset=0" in str(seen[2].url) and "limit=100" in str(seen[2].url)

    from datetime import UTC, datetime

    s = location_to_station(
        locations[0], "operator:acme", LicenseClass.RESTRICTED_COMMERCIAL, datetime.now(UTC)
    )
    assert s.name == "Expressway Food Plaza" and s.operator == "Acme"
    assert len(s.evses) == 1 and s.evses[0].max_kw == 120.0 and s.evses[0].current == "DC"
    assert s.evses[0].connectors[0].standard == ConnectorStandard.CCS2
    assert s.opening_hours == "24/7" and s.location.lat == pytest.approx(18.8012)
    ac = location_to_station(
        locations[1], "operator:acme", LicenseClass.RESTRICTED_COMMERCIAL, datetime.now(UTC)
    )
    assert ac.evses[0].current == "AC" and ac.evses[0].max_kw == pytest.approx(22.2)

    cdrs = [o for c in await client.all("cdrs") if (o := cdr_obs(c))]
    assert len(cdrs) == 1 and cdrs[0].kwh == 31.5 and cdrs[0].location_id == "LOC1"
    await client.aclose()


async def test_errors() -> None:
    with pytest.raises(OCPIError):
        OCPIClient(f"{BASE}/versions", "")
    seen: list[httpx.Request] = []
    client = OCPIClient(f"{BASE}/versions", "t", transport=operator(seen))
    with pytest.raises(OCPIError, match="sessions"):
        await client.all("sessions")

    def old(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=json.dumps(_env([{"version": "2.1.1", "url": "x"}])))

    legacy = OCPIClient(f"{BASE}/versions", "t", transport=httpx.MockTransport(old))
    with pytest.raises(OCPIError, match="2.2.1"):
        await legacy.endpoints()


def test_power_and_sessions() -> None:
    assert connector_kw({"max_voltage": 230, "max_amperage": 16, "power_type": "AC_1_PHASE"}) == 3.7
    assert connector_kw({"power_type": "DC"}) is None
    obs = session_obs(
        {
            "id": "S1",
            "start_date_time": "2026-09-01T10:00:00",
            "kwh": 12,
            "location_id": "LOC1",
            "status": "COMPLETED",
        }
    )
    assert obs is not None and obs.start_at.tzinfo is not None
    assert session_obs({"id": "S2", "start_date_time": None, "kwh": 1}) is None

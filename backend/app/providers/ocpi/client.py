"""OCPI 2.2.1 client for charge point operators' feeds (Section 7.1 row 23).

We act as an eMSP-style receiver pulling from the operator (CPO, SENDER role):
- `versions` discovery: versions URL -> 2.2.1 details -> module endpoint URLs;
- `Authorization: Token <base64(token)>` (OCPI 2.2 encodes the token in base64);
- paginated GETs with offset/limit, following the `Link: <...>; rel="next"` header;
- the response envelope {data, status_code, status_message, timestamp}, where
  status_code 1000 is success.

Locations map to the common ChargingStation model (staged like any charger source);
sessions and CDRs become observed charging sessions for utilisation.
"""

from __future__ import annotations

import base64
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from app.providers.base import (
    EVSE,
    ChargingStation,
    Connector,
    ConnectorStandard,
    LicenseClass,
    Point,
)

VERSION = "2.2.1"
PAGE_LIMIT = 100
_NEXT = re.compile(r'<([^>]+)>\s*;\s*rel="?next"?')

# OCPI ConnectorType -> our standards.
_STANDARDS = {
    "IEC_62196_T2_COMBO": ConnectorStandard.CCS2,
    "IEC_62196_T2": ConnectorStandard.TYPE2_AC,
    "CHADEMO": ConnectorStandard.CHADEMO,
    "GBT_DC": ConnectorStandard.GBT,
    "GBT_AC": ConnectorStandard.GBT,
    "IEC_62196_T1": ConnectorStandard.TYPE1_AC,
    "IEC_62196_T1_COMBO": ConnectorStandard.UNKNOWN,
    "DOMESTIC_M": ConnectorStandard.DOMESTIC_PLUG,
}


class OCPIError(RuntimeError):
    pass


@dataclass(frozen=True)
class ObservedSession:
    kind: str  # session | cdr
    external_id: str
    location_id: str
    evse_uid: str | None
    start_at: datetime
    end_at: datetime | None
    kwh: float
    status: str | None


def _dt(v: str | None) -> datetime | None:
    if not v:
        return None
    d = datetime.fromisoformat(v.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def connector_kw(c: dict[str, Any]) -> float | None:
    if c.get("max_electric_power"):
        return round(float(c["max_electric_power"]) / 1000, 1)
    v, a = c.get("max_voltage"), c.get("max_amperage")
    if not v or not a:
        return None
    phases = 3 if c.get("power_type") == "AC_3_PHASE" else 1
    factor = 3**0.5 if phases == 3 else 1.0  # line-to-line voltage for 3-phase AC
    return round(float(v) * float(a) * factor / 1000, 1)


def location_to_station(
    loc: dict[str, Any], source: str, license_class: LicenseClass, retrieved_at: datetime
) -> ChargingStation:
    evses = []
    for e in loc.get("evses") or []:
        if e.get("status") == "REMOVED":
            continue
        connectors = [
            Connector(
                standard=_STANDARDS.get(c.get("standard", ""), ConnectorStandard.UNKNOWN),
                max_kw=connector_kw(c),
            )
            for c in e.get("connectors") or []
        ]
        kws = [c.max_kw for c in connectors if c.max_kw is not None]
        types = {c.get("power_type") for c in e.get("connectors") or []}
        evses.append(
            EVSE(
                id=str(e.get("uid")),
                max_kw=max(kws) if kws else None,
                current="DC" if "DC" in types else ("AC" if types - {None} else None),
                connectors=connectors,
            )
        )
    coords = loc["coordinates"]
    address = ", ".join(x for x in (loc.get("address"), loc.get("city")) if x)
    return ChargingStation(
        id=str(loc["id"]),
        source=source,
        license_class=license_class,
        retrieved_at=retrieved_at,
        confidence="HIGH",  # operator's own record of its site
        name=loc.get("name"),
        operator=(loc.get("operator") or {}).get("name"),
        location=Point(lat=float(coords["latitude"]), lng=float(coords["longitude"])),
        evses=evses,
        address=address or None,
        access="public" if loc.get("publish", True) else "private",
        status=None,
        opening_hours="24/7" if (loc.get("opening_times") or {}).get("twentyfourseven") else None,
    )


def session_obs(s: dict[str, Any]) -> ObservedSession | None:
    start = _dt(s.get("start_date_time"))
    if start is None or s.get("kwh") is None:
        return None
    return ObservedSession(
        "session",
        str(s["id"]),
        str(s.get("location_id")),
        s.get("evse_uid"),
        start,
        _dt(s.get("end_date_time")),
        float(s["kwh"]),
        s.get("status"),
    )


def cdr_obs(c: dict[str, Any]) -> ObservedSession | None:
    start = _dt(c.get("start_date_time"))
    loc = c.get("cdr_location") or {}
    if start is None or c.get("total_energy") is None or not loc.get("id"):
        return None
    return ObservedSession(
        "cdr",
        str(c["id"]),
        str(loc["id"]),
        loc.get("evse_uid"),
        start,
        _dt(c.get("end_date_time")),
        float(c["total_energy"]),
        "COMPLETED",
    )


class OCPIClient:
    def __init__(
        self, versions_url: str, token: str, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        if not token:
            raise OCPIError("no OCPI token configured")
        encoded = base64.b64encode(token.encode()).decode()
        self._versions_url = versions_url
        self._client = httpx.AsyncClient(
            headers={"Authorization": f"Token {encoded}", "Accept": "application/json"},
            timeout=60.0,
            transport=transport,
        )
        self._endpoints: dict[str, str] | None = None

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(self, url: str, params: dict[str, Any] | None = None) -> httpx.Response:
        r = await self._client.get(url, params=params)
        if r.status_code >= 400:
            raise OCPIError(f"GET {url} -> HTTP {r.status_code}")
        body = r.json()
        if body.get("status_code") != 1000:
            raise OCPIError(
                f"GET {url} -> OCPI {body.get('status_code')}: {body.get('status_message')}"
            )
        return r

    async def endpoints(self) -> dict[str, str]:
        if self._endpoints is None:
            versions = (await self._get(self._versions_url)).json()["data"]
            match = next((v for v in versions if v.get("version") == VERSION), None)
            if match is None:
                raise OCPIError(
                    f"operator doesn't offer OCPI {VERSION}: {[v.get('version') for v in versions]}"
                )
            details = (await self._get(match["url"])).json()["data"]
            self._endpoints = {
                e["identifier"]: e["url"]
                for e in details.get("endpoints", [])
                if e.get("role", "SENDER") == "SENDER"
            }
        return self._endpoints

    async def pages(
        self, module: str, date_from: datetime | None = None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        url = (await self.endpoints()).get(module)
        if url is None:
            raise OCPIError(f"operator doesn't expose the {module} module")
        params: dict[str, Any] | None = {"offset": 0, "limit": PAGE_LIMIT}
        if date_from is not None and params is not None:
            params["date_from"] = date_from.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        next_url: str | None = url
        while next_url:
            r = await self._get(next_url, params)
            yield list(r.json()["data"] or [])
            m = _NEXT.search(r.headers.get("Link", ""))
            next_url, params = (m.group(1), None) if m else (None, None)

    async def all(self, module: str, date_from: datetime | None = None) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        async for page in self.pages(module, date_from):
            out.extend(page)
        return out

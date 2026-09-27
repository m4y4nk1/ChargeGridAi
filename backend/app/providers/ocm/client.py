"""Open Charge Map v3 ChargerProvider (Section 7.1 row 2).

Uses compact responses plus one /referencedata call to resolve IDs into
names. OCM's own contributed data is CC BY 4.0, but some records are
imported from third-party data providers under other terms; per Section 7.1
each record's DataProvider licence is checked, and records that aren't
openly licensed are dropped at ingestion (and counted) rather than stored.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

from app.providers.base import BBox, ChargingStation, ConnectorStandard, LicenseClass, Point
from app.providers.charger_normalize import Socket, evses_from_sockets

BASE_URL = "https://api.openchargemap.io/v3"
MAX_RESULTS = 5000
_OPEN_LICENSE_MARKERS = ("cc by", "cc0", "odbl", "creative commons", "open data", "open government")


class OCMNotConfigured(RuntimeError):
    pass


def connection_standard(title: str | None) -> ConnectorStandard:
    t = (title or "").lower()
    if "ccs" in t and "type 2" in t:
        return ConnectorStandard.CCS2
    if "chademo" in t:
        return ConnectorStandard.CHADEMO
    if "gb-t" in t or "gb/t" in t:
        return ConnectorStandard.GBT
    if "ac001" in t:
        return ConnectorStandard.BHARAT_AC001
    if "dc001" in t:
        return ConnectorStandard.BHARAT_DC001
    if "type 2" in t or "mennekes" in t:
        return ConnectorStandard.TYPE2_AC
    if "type 1" in t or "j1772" in t:
        return ConnectorStandard.TYPE1_AC
    if "domestic" in t or "industrial" in t:
        return ConnectorStandard.DOMESTIC_PLUG
    return ConnectorStandard.UNKNOWN


def _access(usage_title: str | None) -> str | None:
    t = (usage_title or "").lower()
    if not t:
        return None
    if t.startswith("private"):
        return "private"
    if "membership" in t or "restricted" in t or "customers" in t:
        return "semi"
    if t.startswith("public"):
        return "public"
    return None


@dataclass
class ReferenceData:
    connection_types: dict[int, str]
    current_types: dict[int, str]
    operators: dict[int, str]
    usage_types: dict[int, str]
    status_types: dict[int, str]
    provider_is_open: dict[int, bool]

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> ReferenceData:
        def titles(key: str) -> dict[int, str]:
            return {int(item["ID"]): item.get("Title") or "" for item in payload.get(key, [])}

        return cls(
            connection_types=titles("ConnectionTypes"),
            current_types=titles("CurrentTypes"),
            operators=titles("Operators"),
            usage_types=titles("UsageTypes"),
            status_types=titles("StatusTypes"),
            provider_is_open={
                int(p["ID"]): any(
                    m in (p.get("License") or "").lower() for m in _OPEN_LICENSE_MARKERS
                )
                for p in payload.get("DataProviders", [])
            },
        )


@dataclass
class OCMFetchResult:
    stations: list[ChargingStation]
    dropped_non_open: int = 0
    raw: list[dict[str, Any]] = field(default_factory=list)


def normalise_poi(
    poi: dict[str, Any], ref: ReferenceData, retrieved_at: datetime
) -> ChargingStation:
    address = poi.get("AddressInfo") or {}
    sockets = []
    for conn in poi.get("Connections") or []:
        current_title = ref.current_types.get(conn.get("CurrentTypeID") or -1, "")
        sockets.append(
            Socket(
                standard=connection_standard(
                    ref.connection_types.get(conn.get("ConnectionTypeID") or -1)
                ),
                quantity=int(conn.get("Quantity") or 1),
                max_kw=float(conn["PowerKW"]) if conn.get("PowerKW") else None,
                current="DC"
                if current_title.upper().startswith("DC")
                else "AC"
                if current_title.upper().startswith("AC")
                else None,
            )
        )
    record_id = str(poi["ID"])
    operator = ref.operators.get(poi.get("OperatorID") or -1)
    if operator and operator.lower().startswith("(unknown"):
        operator = None
    return ChargingStation(
        id=record_id,
        source="ocm",
        license_class=LicenseClass.OPEN,
        retrieved_at=retrieved_at,
        confidence="MEDIUM",
        name=address.get("Title"),
        operator=operator,
        location=Point(lat=float(address["Latitude"]), lng=float(address["Longitude"])),
        evses=evses_from_sockets(sockets, poi.get("NumberOfPoints"), f"ocm{record_id}"),
        address=", ".join(v for k in ("AddressLine1", "Town") if (v := address.get(k))) or None,
        access=_access(ref.usage_types.get(poi.get("UsageTypeID") or -1)),
        status=ref.status_types.get(poi.get("StatusTypeID") or -1) or None,
    )


class OCMChargerProvider:
    name = "ocm"
    license_class = LicenseClass.OPEN

    def __init__(self, api_key: str, client: httpx.AsyncClient | None = None) -> None:
        if not api_key:
            raise OCMNotConfigured(
                "OCM_API_KEY is not set (free key: https://openchargemap.org/site/develop/api)"
            )
        self._client = client or httpx.AsyncClient(timeout=60.0)
        self._headers = {"X-API-Key": api_key, "User-Agent": "ChargeGridAI/0.1"}

    @retry(
        retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
        wait=wait_exponential_jitter(initial=1, max=30),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    async def _get(self, path: str, params: dict[str, Any]) -> Any:
        response = await self._client.get(f"{BASE_URL}{path}", params=params, headers=self._headers)
        if response.status_code in (401, 403):
            raise OCMNotConfigured(f"OCM rejected the API key ({response.status_code})")
        response.raise_for_status()
        return response.json()

    async def fetch_region(self, bbox: BBox) -> OCMFetchResult:
        retrieved_at = datetime.now(UTC)
        ref = ReferenceData.from_payload(await self._get("/referencedata", {"countrycode": "IN"}))
        raw = await self._fetch_bbox(bbox)

        result = OCMFetchResult(stations=[], raw=raw)
        for poi in raw:
            if not ref.provider_is_open.get(poi.get("DataProviderID") or -1, False):
                result.dropped_non_open += 1
                continue
            result.stations.append(normalise_poi(poi, ref, retrieved_at))
        return result

    async def _fetch_bbox(self, bbox: BBox) -> list[dict[str, Any]]:
        params = {
            "countrycode": "IN",
            "boundingbox": f"({bbox.max_lat},{bbox.min_lng}),({bbox.min_lat},{bbox.max_lng})",
            "maxresults": MAX_RESULTS,
            "compact": "true",
            "verbose": "false",
        }
        items: list[dict[str, Any]] = await self._get("/poi", params)
        if len(items) < MAX_RESULTS:
            return items
        # Truncated: split into quadrants rather than rely on undocumented paging.
        mid_lat, mid_lng = (bbox.min_lat + bbox.max_lat) / 2, (bbox.min_lng + bbox.max_lng) / 2
        seen: dict[int, dict[str, Any]] = {}
        for quad in (
            BBox(min_lat=bbox.min_lat, min_lng=bbox.min_lng, max_lat=mid_lat, max_lng=mid_lng),
            BBox(min_lat=bbox.min_lat, min_lng=mid_lng, max_lat=mid_lat, max_lng=bbox.max_lng),
            BBox(min_lat=mid_lat, min_lng=bbox.min_lng, max_lat=bbox.max_lat, max_lng=mid_lng),
            BBox(min_lat=mid_lat, min_lng=mid_lng, max_lat=bbox.max_lat, max_lng=bbox.max_lng),
        ):
            for poi in await self._fetch_bbox(quad):
                seen[int(poi["ID"])] = poi
        return list(seen.values())

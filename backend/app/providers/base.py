"""Typed provider interfaces (Section 7.2 of the project brief).

Business logic depends only on these Protocols and the normalised Pydantic
models they return — never on a specific provider's raw payload. Concrete
adapters (google/, mappls/, osm/, ...) implement these against real APIs
starting Phase 1+; this module carries no live network code.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel


class LicenseClass(StrEnum):
    OPEN = "OPEN"
    GOVERNMENT = "GOVERNMENT"
    RESTRICTED_GOOGLE = "RESTRICTED_GOOGLE"
    RESTRICTED_COMMERCIAL = "RESTRICTED_COMMERCIAL"
    SYNTHETIC = "SYNTHETIC"


class Point(BaseModel):
    lat: float
    lng: float


class BBox(BaseModel):
    min_lat: float
    min_lng: float
    max_lat: float
    max_lng: float


class Polygon(BaseModel):
    coordinates: list[list[tuple[float, float]]]


class ProvenancedModel(BaseModel):
    source: str
    license_class: LicenseClass
    retrieved_at: datetime
    confidence: str  # HIGH | MEDIUM | LOW | NONE


class POI(ProvenancedModel):
    id: str
    category: str
    name: str
    location: Point


class ConnectorStandard(StrEnum):
    CCS2 = "CCS2"
    TYPE2_AC = "TYPE2_AC"
    CHADEMO = "CHADEMO"
    LECCS = "LECCS"
    BHARAT_AC001 = "BHARAT_AC001"
    BHARAT_DC001 = "BHARAT_DC001"
    GBT = "GBT"
    TYPE1_AC = "TYPE1_AC"
    BATTERY_SWAP = "BATTERY_SWAP"
    DOMESTIC_PLUG = "DOMESTIC_PLUG"
    UNKNOWN = "UNKNOWN"


class Connector(BaseModel):
    standard: ConnectorStandard
    max_kw: float | None = None


class EVSE(BaseModel):
    """A charge point. `count` > 1 means N identical charge points reported as one row."""

    id: str
    max_kw: float | None = None
    current: str | None = None  # AC | DC | None when the source doesn't say
    count: int = 1
    connectors: list[Connector] = []


class ChargingStation(ProvenancedModel):
    """One source's view of a station, before cross-source fusion (Section 7.3)."""

    id: str
    name: str | None
    operator: str | None
    location: Point
    evses: list[EVSE]
    address: str | None = None
    host_type: str | None = None
    access: str | None = None  # public | semi | private
    status: str | None = None
    opening_hours: str | None = None


class Route(BaseModel):
    distance_m: float
    duration_s: float
    geometry: Polygon | None = None


class TimeDistanceMatrix(BaseModel):
    origins: list[Point]
    destinations: list[Point]
    durations_s: list[list[float]]
    distances_m: list[list[float]]


class SolarPotential(BaseModel):
    usable_area_m2: float
    annual_kwh: float
    method: str


class RegistrationAggregate(BaseModel):
    rto_code: str
    month: str
    fuel: str
    segment: str
    count: int


class POICategory(StrEnum):
    EV_CHARGER = "EV_CHARGER"
    FUEL = "FUEL"
    PARKING = "PARKING"
    MALL = "MALL"
    HOTEL = "HOTEL"
    RESTAURANT = "RESTAURANT"
    SUPERMARKET = "SUPERMARKET"
    HOSPITAL = "HOSPITAL"
    OFFICE_PARK = "OFFICE_PARK"
    TRANSIT_HUB = "TRANSIT_HUB"
    INDUSTRIAL = "INDUSTRIAL"


class Mode(StrEnum):
    DRIVE = "DRIVE"
    WALK = "WALK"


class POIProvider(Protocol):
    name: str
    license_class: LicenseClass

    async def search_nearby(
        self, center: Point, radius_m: int, categories: list[POICategory]
    ) -> list[POI]: ...

    async def search_text(self, query: str, bias: Polygon | None) -> list[POI]: ...


class ChargerProvider(Protocol):
    async def fetch_region(self, bbox: BBox) -> list[ChargingStation]: ...


class RoutingProvider(Protocol):
    async def route(
        self, o: Point, d: Point, depart_at: datetime | None, traffic: bool
    ) -> Route: ...

    async def matrix(
        self,
        origins: list[Point],
        dests: list[Point],
        depart_at: datetime | None,
    ) -> TimeDistanceMatrix: ...

    async def isochrone(self, center: Point, minutes: list[int], mode: Mode) -> list[Polygon]: ...


class SolarProvider(Protocol):
    async def site_potential(
        self, point: Point, footprint: Polygon | None
    ) -> SolarPotential | None: ...


class RegistrationProvider(Protocol):
    async def load_snapshot(self, file_or_query: str) -> list[RegistrationAggregate]: ...

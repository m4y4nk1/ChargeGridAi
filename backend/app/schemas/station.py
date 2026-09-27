from datetime import datetime
from typing import Any

from pydantic import BaseModel


class StationSource(BaseModel):
    source_id: str
    source_name: str
    license_class: str
    attribution: str | None
    source_record_id: str
    match_score: float
    retrieved_at: datetime | None
    record: dict[str, Any]


class StationConnector(BaseModel):
    standard: str
    max_kw: float | None


class StationEvse(BaseModel):
    max_kw: float | None
    current: str | None
    count: int
    power_band: str
    connectors: list[StationConnector]


class StationDetail(BaseModel):
    id: str
    name: str | None
    operator: str | None
    lat: float
    lng: float
    access: str | None
    status: str | None
    opening_hours: str | None
    license_class: str
    confidence: str
    evses: list[StationEvse]
    sources: list[StationSource]
    conflicts: list[dict[str, Any]]


class CountRow(BaseModel):
    key: str
    stations: int = 0
    charge_points: int = 0
    connectors: int = 0


class StationStats(BaseModel):
    region_id: int | None
    stations: int
    charge_points: int
    connectors: int
    by_operator: list[CountRow]
    by_power_band: list[CountRow]
    by_connector: list[CountRow]
    evidence_ids: list[str]
    note: str

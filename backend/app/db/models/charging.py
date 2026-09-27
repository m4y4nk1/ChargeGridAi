"""Fused charging-station entities (Section 8.2, fusion rules in Section 7.3).

Stations, EVSEs (charge points) and connectors are separate tables so that
counts at each level are never conflated.
"""

import uuid
from typing import Any

from geoalchemy2 import Geometry
from sqlalchemy import Float, ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.common import TimestampMixin


class ChargingStation(TimestampMixin, Base):
    __tablename__ = "charging_station"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str | None] = mapped_column(Text)
    operator: Mapped[str | None] = mapped_column(Text, index=True)
    host_type: Mapped[str | None] = mapped_column(Text)
    geom: Mapped[Any] = mapped_column(Geometry("POINT", srid=4326))
    access: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(Text)
    opening_hours: Mapped[str | None] = mapped_column(Text)
    license_class: Mapped[str] = mapped_column(Text)
    confidence: Mapped[str] = mapped_column(Text)
    # Section 7.3.6: disagreements between sources are recorded, not hidden.
    conflicts: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)


class ChargerSourceRecord(TimestampMixin, Base):
    """Latest normalised record per source, staged between ingestion and fusion."""

    __tablename__ = "charger_source_record"

    source_id: Mapped[str] = mapped_column(ForeignKey("data_source.id"), primary_key=True)
    source_record_id: Mapped[str] = mapped_column(Text, primary_key=True)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("dataset_snapshot.id"))
    record: Mapped[dict[str, Any]] = mapped_column(JSONB)
    geom: Mapped[Any] = mapped_column(Geometry("POINT", srid=4326))


class Evse(TimestampMixin, Base):
    __tablename__ = "evse"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    station_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("charging_station.id", ondelete="CASCADE"), index=True
    )
    max_kw: Mapped[float | None] = mapped_column(Float)
    current: Mapped[str | None] = mapped_column(Text)
    count: Mapped[int] = mapped_column(Integer)


class Connector(TimestampMixin, Base):
    __tablename__ = "connector"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    evse_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evse.id", ondelete="CASCADE"), index=True
    )
    standard: Mapped[str] = mapped_column(Text)
    max_kw: Mapped[float | None] = mapped_column(Float)


class StationSourceLink(TimestampMixin, Base):
    """One row per contributing source record — the provenance behind a station."""

    __tablename__ = "station_source_link"

    source_id: Mapped[str] = mapped_column(ForeignKey("data_source.id"), primary_key=True)
    source_record_id: Mapped[str] = mapped_column(Text, primary_key=True)
    station_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("charging_station.id", ondelete="CASCADE"), index=True
    )
    snapshot_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("dataset_snapshot.id"))
    match_score: Mapped[float] = mapped_column(Float)
    record: Mapped[dict[str, Any]] = mapped_column(JSONB)

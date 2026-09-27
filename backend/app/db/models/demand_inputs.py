"""Registration and population inputs to the demand engine (Section 8.2)."""

import uuid
from datetime import date
from typing import Any

from geoalchemy2 import Geometry
from sqlalchemy import BigInteger, Date, Float, ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.common import H3Index, TimestampMixin


class VehicleRegistrationAgg(TimestampMixin, Base):
    __tablename__ = "vehicle_registration_agg"

    rto_code: Mapped[str] = mapped_column(Text, primary_key=True)
    month: Mapped[date] = mapped_column(Date, primary_key=True)
    fuel: Mapped[str] = mapped_column(Text, primary_key=True)
    vehicle_class: Mapped[str] = mapped_column(Text, primary_key=True)
    segment: Mapped[str] = mapped_column(Text)
    ev_type: Mapped[str | None] = mapped_column(Text)
    count: Mapped[int] = mapped_column(Integer)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("dataset_snapshot.id"), index=True)


class RtoAreaMap(TimestampMixin, Base):
    """RTO jurisdiction -> admin boundary weights. admin_boundary is osm2pgsql-owned, so no FK."""

    __tablename__ = "rto_area_map"

    rto_code: Mapped[str] = mapped_column(Text, primary_key=True)
    admin_boundary_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    weight: Mapped[float] = mapped_column(Float)


class H3Cell(TimestampMixin, Base):
    __tablename__ = "h3_cell"

    h3: Mapped[str] = mapped_column(H3Index, primary_key=True)
    res: Mapped[int] = mapped_column(Integer)
    region_id: Mapped[str] = mapped_column(Text, index=True)
    geom: Mapped[Any] = mapped_column(Geometry("POLYGON", srid=4326))


class RegionCell(Base):
    """Which modelled region(s) a cell belongs to; regions can overlap (config/regions.yaml)."""

    __tablename__ = "region_cell"

    region_id: Mapped[str] = mapped_column(Text, primary_key=True)
    h3: Mapped[str] = mapped_column(H3Index, primary_key=True)


class H3CellFeature(TimestampMixin, Base):
    __tablename__ = "h3_cell_feature"

    h3: Mapped[str] = mapped_column(H3Index, primary_key=True)
    year: Mapped[int] = mapped_column(Integer, primary_key=True)
    scenario: Mapped[str] = mapped_column(Text, primary_key=True)
    """One row per (cell, year, scenario).

    Each scenario value is owned by exactly one job, which replaces its own rows:
    - 'observed': population at its source vintage (ingest_population)
    - 'history':  EV stock for observed years (run_demand)
    - 'slow' / 'base' / 'fast', and '<case>x<multiplier>' what-ifs: forecasts
      (run_demand)
    Energy columns are kWh per day.
    """

    population: Mapped[float | None] = mapped_column(Float)
    ev_stock_p10: Mapped[float | None] = mapped_column(Float)
    ev_stock_p50: Mapped[float | None] = mapped_column(Float)
    ev_stock_p90: Mapped[float | None] = mapped_column(Float)
    # {segment: [p10, p50, p90]}
    ev_stock_by_segment: Mapped[dict[str, list[float]] | None] = mapped_column(JSONB)
    public_kwh_p10: Mapped[float | None] = mapped_column(Float)
    public_kwh_p50: Mapped[float | None] = mapped_column(Float)
    public_kwh_p90: Mapped[float | None] = mapped_column(Float)
    sessions_p50: Mapped[float | None] = mapped_column(Float)
    # 2SFCA at P50 demand against existing supply (engines/supply_gap.py)
    access_ratio: Mapped[float | None] = mapped_column(Float)
    served_kwh: Mapped[float | None] = mapped_column(Float)
    gap_kwh: Mapped[float | None] = mapped_column(Float)
    snapshot_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)), default=list)


class SolarResource(TimestampMixin, Base):
    """Representative-day solar resource (Section 9.9 fallback): the mean hourly global
    horizontal irradiance and temperature for each month, from NASA POWER."""

    __tablename__ = "solar_resource"

    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("dataset_snapshot.id", ondelete="CASCADE"), primary_key=True
    )
    month: Mapped[int] = mapped_column(Integer, primary_key=True)
    hour: Mapped[int] = mapped_column(Integer, primary_key=True)  # local solar time
    ghi_wm2: Mapped[float] = mapped_column(Float)
    temp_c: Mapped[float] = mapped_column(Float)
    days: Mapped[int] = mapped_column(Integer)
    lat: Mapped[float] = mapped_column(Float)
    lng: Mapped[float] = mapped_column(Float)

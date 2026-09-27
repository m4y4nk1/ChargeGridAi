"""Observed charging activity from operator feeds (OCPI sessions/CDRs), daily station
utilisation derived from it, and calibrated station predictions (Section 9.2 step 5)."""

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.common import TimestampMixin


class ChargingSessionObs(TimestampMixin, Base):
    __tablename__ = "charging_session_obs"
    __table_args__ = (UniqueConstraint("source_id", "kind", "external_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(Text, index=True)  # operator:<id>
    kind: Mapped[str] = mapped_column(Text)  # session | cdr
    external_id: Mapped[str] = mapped_column(Text)
    location_id: Mapped[str] = mapped_column(Text)
    evse_uid: Mapped[str | None] = mapped_column(Text)
    station_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("charging_station.id", ondelete="SET NULL"), index=True
    )
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    kwh: Mapped[float] = mapped_column(Float)
    status: Mapped[str | None] = mapped_column(Text)
    snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("dataset_snapshot.id", ondelete="SET NULL")
    )


class StationUtilisationDaily(Base):
    __tablename__ = "station_utilisation_daily"

    station_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("charging_station.id", ondelete="CASCADE"),
        primary_key=True,
    )
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    kwh: Mapped[float] = mapped_column(Float)
    sessions: Mapped[int] = mapped_column(Integer)
    source_id: Mapped[str] = mapped_column(Text)


class StationPrediction(TimestampMixin, Base):
    """Parametric and calibrated daily energy per observed station, side by side."""

    __tablename__ = "station_prediction"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    calibration_run: Mapped[str] = mapped_column(Text, index=True)  # MLflow run id
    station_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("charging_station.id", ondelete="CASCADE"), index=True
    )
    observed_kwh_per_day: Mapped[float] = mapped_column(Float)
    parametric_kwh_per_day: Mapped[float] = mapped_column(Float)
    calibrated_kwh_per_day: Mapped[float] = mapped_column(Float)
    features: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

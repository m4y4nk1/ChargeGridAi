"""DISCOM grid asset ratings and loadings (Section 7.1 row 22). Contractual data:
licence RESTRICTED_COMMERCIAL, the raw file is never kept in the raw lake, and assets
are never drawn on the open map."""

import uuid
from datetime import date
from typing import Any

from geoalchemy2 import Geometry
from sqlalchemy import Date, Float, ForeignKey, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.common import TimestampMixin


class GridAssetRated(TimestampMixin, Base):
    __tablename__ = "grid_asset_rated"

    id: Mapped[str] = mapped_column(Text, primary_key=True)  # "<discom>:<asset code>"
    discom: Mapped[str] = mapped_column(Text, index=True)
    asset_type: Mapped[str] = mapped_column(Text)  # transformer | substation
    name: Mapped[str | None] = mapped_column(Text)
    voltage_kv: Mapped[float | None] = mapped_column(Float)
    rated_kva: Mapped[float] = mapped_column(Float)
    loading_limit: Mapped[float | None] = mapped_column(Float)
    base_peak_kva: Mapped[float] = mapped_column(Float)
    base_hourly_kva: Mapped[list[float] | None] = mapped_column(JSONB)
    measured_on: Mapped[date | None] = mapped_column(Date)
    license_class: Mapped[str] = mapped_column(Text, default="RESTRICTED_COMMERCIAL")
    snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("dataset_snapshot.id", ondelete="SET NULL")
    )
    extra: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    geom: Mapped[Any] = mapped_column(Geometry("POINT", srid=4326))

"""Gazetteer of OSM place nodes (city, town, suburb, village ...), used to resolve place
names in agent requests ("Mumbai to Pune") to coordinates."""

from typing import Any

from geoalchemy2 import Geometry
from sqlalchemy import BigInteger, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Place(Base):
    __tablename__ = "osm_place"

    osm_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(Text, index=True)
    name_en: Mapped[str | None] = mapped_column(Text)
    place: Mapped[str] = mapped_column(Text)  # city | town | suburb | village | ...
    population: Mapped[int | None] = mapped_column(Integer)
    alt_names: Mapped[list[str]] = mapped_column(JSONB, default=list)
    geom: Mapped[Any] = mapped_column(Geometry("POINT", srid=4326))

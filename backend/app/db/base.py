from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base for Alembic-managed application tables.

    Deliberately does NOT cover road_segment/poi/grid_asset/admin_boundary —
    those are owned by the osm2pgsql flex import
    (see app/providers/osm/import_pbf.py).
    """

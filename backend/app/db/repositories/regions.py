"""Read access to `admin_boundary` (Section 8.2).

This table is owned by the osm2pgsql flex import
(`app/providers/osm/flex_style.lua`), not Alembic — see that module's
docstring. This repository only ever reads it.
"""

import json

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.region import Region, RegionSummary

_LIST_SQL = text(
    """
    select osm_id, name, level, admin_level, code
    from admin_boundary
    order by admin_level nulls last, name
    """
)

_GET_SQL = text(
    """
    select osm_id, name, level, admin_level, code, ST_AsGeoJSON(geom) as geometry
    from admin_boundary
    where osm_id = :region_id
    """
)


async def list_regions(session: AsyncSession) -> list[RegionSummary]:
    rows = (await session.execute(_LIST_SQL)).mappings().all()
    return [
        RegionSummary(
            id=row["osm_id"],
            name=row["name"],
            level=row["level"] or "other",
            admin_level=row["admin_level"],
            code=row["code"],
        )
        for row in rows
    ]


async def get_region(session: AsyncSession, region_id: int) -> Region | None:
    row = (await session.execute(_GET_SQL, {"region_id": region_id})).mappings().first()
    if row is None:
        return None
    return Region(
        id=row["osm_id"],
        name=row["name"],
        level=row["level"] or "other",
        admin_level=row["admin_level"],
        code=row["code"],
        geometry=json.loads(row["geometry"]),
    )

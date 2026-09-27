"""Read access to fused charging stations, their EVSEs/connectors, and provenance."""

from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.charger_classes import POWER_BANDS, power_band
from app.db.models.charging import ChargingStation, Connector, Evse, StationSourceLink
from app.db.models.provenance import DatasetSnapshot, DataSource, Evidence
from app.schemas.station import (
    CountRow,
    StationConnector,
    StationDetail,
    StationEvse,
    StationSource,
    StationStats,
)


@dataclass
class StationRow:
    station: ChargingStation
    lat: float
    lng: float
    evses: list[tuple[Evse, list[Connector]]]
    source_ids: list[str]


async def _load(session: AsyncSession, where: list[Any]) -> list[StationRow]:
    rows = (
        await session.execute(
            select(
                ChargingStation, func.ST_Y(ChargingStation.geom), func.ST_X(ChargingStation.geom)
            )
            .where(*where)
            .order_by(ChargingStation.id)
        )
    ).all()
    if not rows:
        return []
    ids = [r[0].id for r in rows]
    evses = (await session.execute(select(Evse).where(Evse.station_id.in_(ids)))).scalars().all()
    connectors = (
        (
            await session.execute(
                select(Connector).where(Connector.evse_id.in_([e.id for e in evses]))
            )
        )
        .scalars()
        .all()
    )
    links = (
        await session.execute(
            select(StationSourceLink.station_id, StationSourceLink.source_id).where(
                StationSourceLink.station_id.in_(ids)
            )
        )
    ).all()

    connectors_by_evse: dict[uuid.UUID, list[Connector]] = defaultdict(list)
    for c in connectors:
        connectors_by_evse[c.evse_id].append(c)
    evses_by_station: dict[uuid.UUID, list[tuple[Evse, list[Connector]]]] = defaultdict(list)
    for e in evses:
        evses_by_station[e.station_id].append((e, connectors_by_evse[e.id]))
    sources_by_station: dict[uuid.UUID, list[str]] = defaultdict(list)
    for station_id, source_id in links:
        sources_by_station[station_id].append(source_id)

    return [
        StationRow(s, lat, lng, evses_by_station[s.id], sorted(set(sources_by_station[s.id])))
        for s, lat, lng in rows
    ]


def _max_kw(row: StationRow) -> float | None:
    kws = [e.max_kw for e, _ in row.evses if e.max_kw is not None]
    return max(kws) if kws else None


def _station_band(row: StationRow) -> str:
    bands = [power_band(e.current, e.max_kw) for e, _ in row.evses]
    known = [b for b in bands if b != "Unknown"]
    return max(known, key=POWER_BANDS.index) if known else "Unknown"


async def stations_geojson(
    session: AsyncSession,
    bbox: tuple[float, float, float, float] | None,
    min_kw: float | None,
    operator: str | None,
) -> dict[str, Any]:
    where: list[Any] = []
    if bbox is not None:
        where.append(ChargingStation.geom.intersects(func.ST_MakeEnvelope(*bbox, 4326)))
    if operator:
        where.append(ChargingStation.operator.ilike(f"%{operator}%"))
    features = []
    for row in await _load(session, where):
        max_kw = _max_kw(row)
        if min_kw is not None and (max_kw is None or max_kw < min_kw):
            continue
        s = row.station
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [row.lng, row.lat]},
                "properties": {
                    "id": str(s.id),
                    "name": s.name,
                    "operator": s.operator,
                    "license_class": s.license_class,
                    "confidence": s.confidence,
                    "charge_points": sum(e.count for e, _ in row.evses),
                    "max_kw": max_kw,
                    "power_band": _station_band(row),
                    "connectors": sorted({c.standard for _, cs in row.evses for c in cs}),
                    "sources": row.source_ids,
                    "has_conflicts": bool(s.conflicts),
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}


async def station_detail(session: AsyncSession, station_id: uuid.UUID) -> StationDetail | None:
    rows = await _load(session, [ChargingStation.id == station_id])
    if not rows:
        return None
    row = rows[0]
    s = row.station
    links = (
        await session.execute(
            select(StationSourceLink, DataSource, DatasetSnapshot.retrieved_at)
            .join(DataSource, DataSource.id == StationSourceLink.source_id)
            .outerjoin(DatasetSnapshot, DatasetSnapshot.id == StationSourceLink.snapshot_id)
            .where(StationSourceLink.station_id == station_id)
            .order_by(StationSourceLink.match_score.desc())
        )
    ).all()
    return StationDetail(
        id=str(s.id),
        name=s.name,
        operator=s.operator,
        lat=row.lat,
        lng=row.lng,
        access=s.access,
        status=s.status,
        opening_hours=s.opening_hours,
        license_class=s.license_class,
        confidence=s.confidence,
        evses=[
            StationEvse(
                max_kw=e.max_kw,
                current=e.current,
                count=e.count,
                power_band=power_band(e.current, e.max_kw),
                connectors=[StationConnector(standard=c.standard, max_kw=c.max_kw) for c in cs],
            )
            for e, cs in row.evses
        ],
        sources=[
            StationSource(
                source_id=link.source_id,
                source_name=source.name,
                license_class=source.license_class,
                attribution=source.attribution,
                source_record_id=link.source_record_id,
                match_score=link.match_score,
                retrieved_at=retrieved_at,
                record=link.record,
            )
            for link, source, retrieved_at in links
        ],
        conflicts=s.conflicts or [],
    )


async def station_stats(session: AsyncSession, region_id: int | None) -> StationStats:
    where: list[Any] = []
    if region_id is not None:
        region_geom = text(
            "(select geom from admin_boundary where osm_id = :region_id)"
        ).bindparams(region_id=region_id)
        where.append(func.ST_Within(ChargingStation.geom, region_geom))
    rows = await _load(session, where)

    by_operator: dict[str, CountRow] = {}
    spellings: dict[str, Counter[str]] = defaultdict(Counter)
    by_band: dict[str, CountRow] = {b: CountRow(key=b) for b in POWER_BANDS}
    by_connector: dict[str, CountRow] = {}
    for row in rows:
        # Case/whitespace-insensitive only; fuzzy merging could hide genuinely distinct CPOs.
        name = " ".join((row.station.operator or "Unknown operator").split())
        op = by_operator.setdefault(name.casefold(), CountRow(key=name))
        spellings[name.casefold()][name] += 1
        op.stations += 1
        for e, cs in row.evses:
            op.charge_points += e.count
            op.connectors += len(cs)
            by_band[power_band(e.current, e.max_kw)].charge_points += e.count
            for c in cs:
                by_connector.setdefault(c.standard, CountRow(key=c.standard)).connectors += 1

    for folded, row_count in by_operator.items():
        row_count.key = spellings[folded].most_common(1)[0][0]

    evidence_ids = (
        (
            await session.execute(
                select(Evidence.id).where(Evidence.subject_type == "charger_fusion")
            )
        )
        .scalars()
        .all()
    )
    return StationStats(
        region_id=region_id,
        stations=len(rows),
        charge_points=sum(e.count for r in rows for e, _ in r.evses),
        connectors=sum(len(cs) for r in rows for _, cs in r.evses),
        by_operator=sorted(by_operator.values(), key=lambda c: (-c.stations, c.key)),
        by_power_band=[c for c in by_band.values() if c.charge_points],
        by_connector=sorted(by_connector.values(), key=lambda c: -c.connectors),
        evidence_ids=[str(i) for i in evidence_ids],
        note=(
            "Stations, charge points (EVSEs) and connectors are counted separately. Where a "
            "source lists connectors but not charge points, connector counts stand in for "
            "charge points per current type."
        ),
    )

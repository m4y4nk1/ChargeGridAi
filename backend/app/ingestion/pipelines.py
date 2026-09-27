"""Ingestion jobs (Section 13). Each is idempotent and writes one snapshot.

Charger sources are staged per source in `charger_source_record`;
`run_charger_fusion` then rebuilds the fused station tables from everything
staged, so re-running any one source's ingestion plus fusion converges to
the same result.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import h3
from geoalchemy2.shape import from_shape
from shapely.geometry import Point as ShapelyPoint
from shapely.geometry import Polygon as ShapelyPolygon
from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.regions import all_regions, default_region_id, get_region
from app.core.settings import get_settings
from app.db.models.charging import (
    ChargerSourceRecord,
    ChargingStation,
    Connector,
    Evse,
    StationSourceLink,
)
from app.db.models.demand_inputs import (
    H3Cell,
    H3CellFeature,
    RegionCell,
    VehicleRegistrationAgg,
)
from app.db.models.provenance import DatasetSnapshot, Evidence
from app.ingestion.fusion import fuse
from app.ingestion.provenance import previous_row_count, quality_report, record_snapshot
from app.providers.base import BBox
from app.providers.base import ChargingStation as StationRecord
from app.providers.government.bee_bhel import load_government_csv
from app.providers.government.vahan import VahanMappings, load_vahan_csv
from app.providers.ocm.client import OCMChargerProvider
from app.providers.osm.chargers import capacity_issue, osm_tags_to_station
from app.providers.rasters.worldpop import aggregate_to_h3


def region_bbox(region_id: str | None = None) -> BBox:
    """A configured region's extent (config/regions.yaml) as a provider BBox."""
    b = get_region(region_id).bbox
    return BBox(min_lat=b.min_lat, min_lng=b.min_lng, max_lat=b.max_lat, max_lng=b.max_lng)


# The default region (Pune Metropolitan Region, Section 1 MVP geography). Kept as names
# for callers that predate the region registry; new code takes a region id.
PMR_REGION_ID = default_region_id()
PMR_BBOX = region_bbox(PMR_REGION_ID)

_STATION_FIELDS = ("name", "operator", "evses", "address", "access", "status")


@dataclass
class JobSummary:
    source_id: str
    snapshot_id: str | None
    rows: int
    notes: dict[str, Any]


def _coordinate(record: StationRecord) -> tuple[float, float]:
    return (record.location.lat, record.location.lng)


async def _stage_charger_records(
    session: AsyncSession,
    source_id: str,
    stations: list[StationRecord],
    raw: bytes,
    raw_filename: str,
    retrieved_at: datetime,
    **report_extra: Any,
) -> JobSummary:
    report = quality_report(
        stations,
        _STATION_FIELDS,
        _coordinate,
        previous_row_count=await previous_row_count(session, source_id, "point"),
        charge_points=sum(e.count for s in stations for e in s.evses),
        connectors=sum(len(e.connectors) for s in stations for e in s.evses),
        **report_extra,
    )
    snapshot = await record_snapshot(
        session,
        source_id=source_id,
        retrieved_at=retrieved_at,
        raw=raw,
        raw_filename=raw_filename,
        row_count=len(stations),
        granularity="point",
        report=report,
    )
    await session.execute(
        delete(ChargerSourceRecord).where(ChargerSourceRecord.source_id == source_id)
    )
    for s in stations:
        session.add(
            ChargerSourceRecord(
                source_id=source_id,
                source_record_id=s.id,
                snapshot_id=snapshot.id,
                record=json.loads(s.model_dump_json()),
                geom=from_shape(ShapelyPoint(s.location.lng, s.location.lat), srid=4326),
            )
        )
    await session.commit()
    return JobSummary(source_id, str(snapshot.id), len(stations), report)


async def ingest_osm_chargers(session: AsyncSession) -> JobSummary:
    """Charging stations from the osm2pgsql `poi` table (category EV_CHARGER)."""
    rows = (
        (
            await session.execute(
                text(
                    "select osm_type, osm_id, attrs, ST_Y(geom) as lat, ST_X(geom) as lng "
                    "from poi where category = 'EV_CHARGER' order by osm_type, osm_id"
                )
            )
        )
        .mappings()
        .all()
    )
    osm_import_at = await session.scalar(
        select(func.max(DatasetSnapshot.retrieved_at)).where(DatasetSnapshot.source_id == "osm")
    )
    retrieved_at = osm_import_at or datetime.now(UTC)
    stations = [
        osm_tags_to_station(
            r["osm_type"], r["osm_id"], r["attrs"] or {}, r["lat"], r["lng"], retrieved_at
        )
        for r in rows
    ]
    raw = json.dumps([dict(r) for r in rows], default=str).encode()
    return await _stage_charger_records(
        session,
        "osm",
        stations,
        raw,
        "osm_chargers.json",
        retrieved_at,
        note="Derived from the latest osm2pgsql import",
        suspect_capacity_tags={
            f"{r['osm_type']}{r['osm_id']}": issue
            for r in rows
            if (issue := capacity_issue(r["attrs"] or {}))
        },
    )


async def ingest_ocm(session: AsyncSession, region_id: str | None = None) -> JobSummary:
    """Open Charge Map for a region. Staging replaces all OCM records, so ingest the widest
    region (it contains the others)."""
    bbox = region_bbox(region_id)
    result = await OCMChargerProvider(get_settings().ocm_api_key).fetch_region(bbox)
    retrieved_at = result.stations[0].retrieved_at if result.stations else datetime.now(UTC)
    return await _stage_charger_records(
        session,
        "ocm",
        result.stations,
        json.dumps(result.raw).encode(),
        "ocm_poi.json",
        retrieved_at,
        dropped_non_open_license=result.dropped_non_open,
    )


async def ingest_government_chargers(
    session: AsyncSession, csv_path: Path, source_id: str = "bee_evyatra"
) -> JobSummary:
    retrieved_at = datetime.fromtimestamp(csv_path.stat().st_mtime, UTC)
    stations = load_government_csv(csv_path, source_id, retrieved_at)
    return await _stage_charger_records(
        session, source_id, stations, csv_path.read_bytes(), csv_path.name, retrieved_at
    )


async def run_charger_fusion(session: AsyncSession) -> JobSummary:
    staged = (await session.execute(select(ChargerSourceRecord))).scalars().all()
    snapshot_of = {(r.source_id, r.source_record_id): r.snapshot_id for r in staged}
    records = [StationRecord.model_validate(r.record) for r in staged]
    fused = fuse(records)

    await session.execute(delete(ChargingStation))  # cascades to evse, connector, links
    for f in fused:
        session.add(
            ChargingStation(
                id=f.id,
                name=f.fields["name"],
                operator=f.fields["operator"],
                host_type=f.fields["host_type"],
                geom=from_shape(ShapelyPoint(f.location[1], f.location[0]), srid=4326),
                access=f.fields["access"],
                status=f.fields["status"],
                opening_hours=f.fields["opening_hours"],
                license_class=str(f.license_class),
                confidence=f.confidence,
                conflicts=f.conflicts,
            )
        )
    await session.flush()
    for f in fused:
        for e in f.evses:
            evse = Evse(station_id=f.id, max_kw=e.max_kw, current=e.current, count=e.count)
            session.add(evse)
            await session.flush()
            for c in e.connectors:
                session.add(Connector(evse_id=evse.id, standard=str(c.standard), max_kw=c.max_kw))
        for member, score in f.members:
            session.add(
                StationSourceLink(
                    source_id=member.source,
                    source_record_id=member.id,
                    station_id=f.id,
                    snapshot_id=snapshot_of.get((member.source, member.id)),
                    match_score=round(score, 4),
                    record=json.loads(member.model_dump_json()),
                )
            )

    counts = {
        "stations": len(fused),
        "charge_points": sum(e.count for f in fused for e in f.evses),
        "connectors": sum(len(e.connectors) for f in fused for e in f.evses),
        "multi_source_stations": sum(1 for f in fused if len({m.source for m, _ in f.members}) > 1),
        "stations_with_conflicts": sum(1 for f in fused if f.conflicts),
        "input_records": len(records),
    }
    snapshot_ids = sorted({sid for sid in snapshot_of.values() if sid is not None})
    await session.execute(delete(Evidence).where(Evidence.subject_type == "charger_fusion"))
    for metric, value in counts.items():
        session.add(
            Evidence(
                subject_type="charger_fusion",
                subject_id="latest",
                metric=metric,
                value_num=float(value),
                unit="count",
                method="fusion_v1 (Section 7.3)",
                snapshot_ids=snapshot_ids,
                confidence="MEDIUM",
            )
        )
    await session.commit()
    return JobSummary("fusion", None, len(fused), counts)


async def ingest_vahan(session: AsyncSession, csv_path: Path, source_id: str) -> JobSummary:
    """Load a normalised VAHAN CSV. Real `vahan` data replaces any synthetic stand-in."""
    if source_id == "synthetic_vahan":
        real_exists = await session.scalar(
            select(func.count())
            .select_from(VehicleRegistrationAgg)
            .join(DatasetSnapshot, VehicleRegistrationAgg.snapshot_id == DatasetSnapshot.id)
            .where(DatasetSnapshot.source_id == "vahan")
        )
        if real_exists:
            raise RuntimeError(
                "Real VAHAN data is loaded; refusing to overwrite it with synthetic data"
            )

    mappings = VahanMappings.load(get_settings().config_dir / "demand" / "segments.yaml")
    rows = load_vahan_csv(csv_path, mappings)
    months = sorted({r.month for r in rows})
    report = quality_report(
        rows,
        ("segment", "ev_type"),
        previous_row_count=await previous_row_count(session, source_id, "RTO x month"),
        rtos=sorted({r.rto_code for r in rows}),
        total_registrations=sum(r.count for r in rows),
        by_segment={
            s: sum(r.count for r in rows if r.segment == s)
            for s in sorted({r.segment for r in rows})
        },
    )
    snapshot = await record_snapshot(
        session,
        source_id=source_id,
        retrieved_at=datetime.now(UTC),
        raw=csv_path.read_bytes(),
        raw_filename=csv_path.name,
        row_count=len(rows),
        granularity="RTO x month",
        report=report,
        period=(months[0], months[-1]) if months else None,
    )
    stale_snapshots = select(DatasetSnapshot.id).where(
        DatasetSnapshot.source_id.in_(["vahan", "synthetic_vahan"])
    )
    await session.execute(
        delete(VehicleRegistrationAgg).where(
            VehicleRegistrationAgg.snapshot_id.in_(stale_snapshots)
        )
    )
    session.add_all(
        VehicleRegistrationAgg(
            rto_code=r.rto_code,
            month=r.month,
            fuel=r.fuel,
            vehicle_class=r.vehicle_class,
            segment=r.segment,
            ev_type=r.ev_type,
            count=r.count,
            snapshot_id=snapshot.id,
        )
        for r in rows
    )
    await session.commit()
    return JobSummary(source_id, str(snapshot.id), len(rows), report)


async def ingest_population(
    session: AsyncSession,
    raster_path: Path,
    year: int,
    grid: str = "100m",
    res: int = 8,
    region_id: str | None = None,
) -> JobSummary:
    region_id = region_id or default_region_id()
    # 1 km pixels are ~one res-8 cell each, so they're split 10x10 before
    # assignment (see aggregate_to_h3) and carry lower confidence.
    subsample, confidence = (1, "MEDIUM") if grid == "100m" else (10, "LOW")
    method = f"worldpop_{grid}_to_h3r{res}" + ("_areal_subsample10" if subsample > 1 else "")
    agg = aggregate_to_h3(raster_path, res, subsample=subsample)
    cell_total = sum(agg.cells.values())
    report = quality_report(
        [],
        (),
        previous_row_count=await previous_row_count(session, "worldpop", f"H3r{res}"),
        h3_res=res,
        cells=len(agg.cells),
        pixel_total=round(agg.pixel_total, 1),
        cell_total=round(cell_total, 1),
        total_preserved=abs(cell_total - agg.pixel_total) <= 1e-6 * max(agg.pixel_total, 1),
        nodata_share=round(agg.nodata_share, 4),
        grid=grid,
        method=method,
        un_adjusted=grid == "1km",
        vintage_note=(
            f"WorldPop {year} estimate; not yet calibrated to Census/ward totals (Phase 3)"
        ),
    )
    report["row_count"] = len(agg.cells)
    snapshot = await record_snapshot(
        session,
        source_id="worldpop",
        retrieved_at=datetime.now(UTC),
        raw=raster_path.read_bytes(),
        raw_filename=raster_path.name,
        row_count=len(agg.cells),
        granularity=f"H3r{res}",
        report=report,
    )

    # Replace, don't merge: a different grid or year can cover different cells.
    region_cells = select(RegionCell.h3).where(RegionCell.region_id == region_id)
    await session.execute(
        delete(H3CellFeature).where(
            H3CellFeature.year == year,
            H3CellFeature.scenario == "observed",
            H3CellFeature.h3.in_(region_cells),
        )
    )
    await session.execute(
        delete(Evidence).where(
            Evidence.subject_type == "region",
            Evidence.subject_id == region_id,
            Evidence.metric == f"population_{year}",
        )
    )
    for cell, population in agg.cells.items():
        boundary = [(lng, lat) for lat, lng in h3.cell_to_boundary(cell)]
        await session.execute(
            insert(H3Cell)
            .values(
                h3=cell,
                res=res,
                region_id=region_id,
                geom=from_shape(ShapelyPolygon(boundary), srid=4326),
            )
            .on_conflict_do_nothing(index_elements=[H3Cell.h3])
        )
        await session.execute(
            insert(RegionCell).values(region_id=region_id, h3=cell).on_conflict_do_nothing()
        )
        await session.execute(
            insert(H3CellFeature)
            .values(
                h3=cell,
                year=year,
                scenario="observed",
                population=population,
                snapshot_ids=[snapshot.id],
            )
            .on_conflict_do_update(
                index_elements=[H3CellFeature.h3, H3CellFeature.year, H3CellFeature.scenario],
                set_={"population": population, "snapshot_ids": [snapshot.id]},
            )
        )
    session.add(
        Evidence(
            subject_type="region",
            subject_id=region_id,
            metric=f"population_{year}",
            value_num=round(cell_total, 1),
            unit="people",
            method=method,
            snapshot_ids=[snapshot.id],
            confidence=confidence,
        )
    )
    await session.commit()
    return JobSummary("worldpop", str(snapshot.id), len(agg.cells), report)


async def ingest_solar(
    session: AsyncSession, year: int, region_id: str | None = None
) -> JobSummary:
    """NASA POWER hourly irradiance for the region centre, reduced to representative days
    (Section 9.9 fallback; Google Solar API is used per site when configured)."""
    from app.db.models.demand_inputs import SolarResource
    from app.providers.nasa.power import fetch_year, representative_days

    centre_lat, centre_lng = get_region(region_id).bbox.centre
    lat, lng = round(centre_lat, 4), round(centre_lng, 4)
    raw = await fetch_year(lat, lng, year)
    rows, counts = representative_days(raw)
    annual_ghi_kwh_m2 = sum(r.ghi_wm2 * r.days for r in rows) / 1000
    report = quality_report(
        [],
        (),
        previous_row_count=await previous_row_count(session, "nasa_power", "month x hour"),
        year=year,
        point={"lat": lat, "lng": lng},
        months=len({r.month for r in rows}),
        annual_ghi_kwh_m2=round(annual_ghi_kwh_m2, 1),
        hours=counts["hours"],
        missing_hours=counts["missing_hours"],
    )
    report["row_count"] = len(rows)
    snapshot = await record_snapshot(
        session,
        source_id="nasa_power",
        retrieved_at=datetime.now(UTC),
        raw=raw,
        raw_filename=f"nasa_power_hourly_{year}_{lat}_{lng}.json",
        row_count=len(rows),
        granularity="month x hour",
        report=report,
        period=(date(year, 1, 1), date(year, 12, 31)),
    )
    session.add_all(
        SolarResource(
            snapshot_id=snapshot.id,
            month=r.month,
            hour=r.hour,
            ghi_wm2=r.ghi_wm2,
            temp_c=r.temp_c,
            days=r.days,
            lat=lat,
            lng=lng,
        )
        for r in rows
    )
    await session.commit()
    return JobSummary("nasa_power", str(snapshot.id), len(rows), report)


async def ingest_ocpi(session: AsyncSession, operator_id: str) -> JobSummary:
    """One operator's OCPI feed: locations are staged and fused like any charger source;
    CDRs (or completed sessions when the operator sends no CDRs) become observed
    sessions, rolled up into daily station utilisation."""
    import os
    from datetime import timedelta

    import yaml

    from app.db.models.operations import ChargingSessionObs, StationUtilisationDaily
    from app.providers.base import LicenseClass
    from app.providers.ocpi.client import OCPIClient, cdr_obs, location_to_station, session_obs

    ops = (
        yaml.safe_load((get_settings().config_dir / "ocpi_operators.yaml").read_text()).get(
            "operators"
        )
        or {}
    )
    if operator_id not in ops:
        raise ValueError(f"unknown operator {operator_id}; configure config/ocpi_operators.yaml")
    op = ops[operator_id]
    source_id = f"operator:{operator_id}"
    license_class = LicenseClass(op.get("license_class", "RESTRICTED_COMMERCIAL"))
    token = os.environ.get(f"OCPI_TOKEN_{operator_id.upper()}", "")
    client = OCPIClient(op["versions_url"], token)
    retrieved_at = datetime.now(UTC)
    try:
        locations = await client.all("locations")
        stations = [
            location_to_station(loc, source_id, license_class, retrieved_at)
            for loc in locations
            if loc.get("coordinates")
        ]
        summary = await _stage_charger_records(
            session,
            source_id,
            stations,
            json.dumps(locations).encode(),
            f"{operator_id}_locations.json",
            retrieved_at,
        )
        await run_charger_fusion(session)

        latest = await session.scalar(
            select(func.max(ChargingSessionObs.start_at)).where(
                ChargingSessionObs.source_id == source_id
            )
        )
        since = (latest - timedelta(days=2)) if latest else retrieved_at - timedelta(days=120)
        modules = set(await client.endpoints())
        if "cdrs" in modules:
            observed = [o for c in await client.all("cdrs", since) if (o := cdr_obs(c))]
        elif "sessions" in modules:
            observed = [
                o
                for s in await client.all("sessions", since)
                if (o := session_obs(s)) and o.status == "COMPLETED"
            ]
        else:
            observed = []
    finally:
        await client.aclose()

    links: dict[str, uuid.UUID] = {
        rec: station
        for rec, station in (
            await session.execute(
                select(StationSourceLink.source_record_id, StationSourceLink.station_id).where(
                    StationSourceLink.source_id == source_id
                )
            )
        ).all()
    }
    snapshot_id = uuid.UUID(summary.snapshot_id) if summary.snapshot_id else None
    for o in observed:
        values = {
            "source_id": source_id,
            "kind": o.kind,
            "external_id": o.external_id,
            "location_id": o.location_id,
            "evse_uid": o.evse_uid,
            "station_id": links.get(o.location_id),
            "start_at": o.start_at,
            "end_at": o.end_at,
            "kwh": o.kwh,
            "status": o.status,
            "snapshot_id": snapshot_id,
        }
        stmt = insert(ChargingSessionObs).values(**values)
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=["source_id", "kind", "external_id"],
                set_={
                    k: v for k, v in values.items() if k not in ("source_id", "kind", "external_id")
                },
            )
        )
    # Re-derive this operator's daily utilisation (CDRs when any exist, else sessions).
    await session.execute(
        delete(StationUtilisationDaily).where(StationUtilisationDaily.source_id == source_id)
    )
    await session.execute(
        text(
            "insert into station_utilisation_daily (station_id, day, kwh, sessions, source_id) "
            "select station_id, (start_at at time zone 'Asia/Kolkata')::date, sum(kwh), "
            "count(*), :src from charging_session_obs where source_id = :src "
            "and station_id is not null and kind = (case when exists (select 1 from "
            "charging_session_obs where source_id = :src and kind = 'cdr') then 'cdr' "
            "else 'session' end) group by 1, 2 "
            "on conflict (station_id, day) do update set kwh = excluded.kwh, "
            "sessions = excluded.sessions, source_id = excluded.source_id"
        ),
        {"src": source_id},
    )
    await session.commit()
    summary.notes["observed_sessions"] = len(observed)
    summary.notes["unmatched_locations"] = sum(1 for o in observed if o.location_id not in links)
    return summary


PLACE_TYPES = ("city", "town", "suburb", "village", "neighbourhood", "locality")


async def ingest_places(session: AsyncSession, pbf: Path) -> int:
    """Rebuild the `osm_place` gazetteer from the PBF's place nodes (osmium extract), so
    agents can resolve names like "Pune" or "Lonavala" without an external geocoder."""
    import subprocess
    import tempfile

    from app.db.models.geo import Place

    with tempfile.TemporaryDirectory() as tmp:
        filtered, out = Path(tmp) / "places.pbf", Path(tmp) / "places.geojsonseq"
        subprocess.run(
            [
                "osmium",
                "tags-filter",
                str(pbf),
                f"n/place={','.join(PLACE_TYPES)}",
                "-o",
                str(filtered),
                "--overwrite",
            ],
            check=True,
        )
        subprocess.run(
            [
                "osmium",
                "export",
                str(filtered),
                "-f",
                "geojsonseq",
                "-o",
                str(out),
                "--overwrite",
                "-a",
                "type,id",
            ],
            check=True,
        )
        rows: dict[int, dict[str, Any]] = {}
        for line in out.read_text().split("\n"):
            if not line.strip("\x1e "):
                continue
            feature = json.loads(line.lstrip("\x1e"))
            props = feature["properties"]
            name = props.get("name:en") or props.get("name")
            if not name or feature["geometry"]["type"] != "Point":
                continue
            lng, lat = feature["geometry"]["coordinates"]
            pop = str(props.get("population", "")).replace(",", "")
            alts = {
                v
                for k, v in props.items()
                if k in ("name", "alt_name", "old_name", "official_name", "alt_name:en")
                and v != name
            }
            rows[int(props["@id"])] = {
                "osm_id": int(props["@id"]),
                "name": name,
                "name_en": props.get("name:en"),
                "place": props["place"],
                "population": int(pop) if pop.isdigit() else None,
                "alt_names": sorted(alts),
                "geom": f"SRID=4326;POINT({lng} {lat})",
            }
    await session.execute(delete(Place))
    if rows:
        await session.execute(insert(Place), list(rows.values()))
    await session.commit()
    return len(rows)


async def prune_admin_boundaries(session: AsyncSession) -> int:
    """Drop boundaries touching no configured region, or with no geometry (a relation
    whose members fell outside every extract). Complete district relations are
    merged in from the zone extract (so ones crossing the clip box keep their geometry),
    which also brings every other district in the zone."""
    boxes = " or ".join(
        f"geom && ST_MakeEnvelope({r.bbox.min_lng}, {r.bbox.min_lat}, "
        f"{r.bbox.max_lng}, {r.bbox.max_lat}, 4326)"
        for r in all_regions()
    )
    deleted = (
        await session.execute(
            text(
                "delete from admin_boundary where geom is null or st_isempty(geom) "
                f"or not ({boxes}) returning osm_id"
            )
        )
    ).all()
    await session.commit()
    return len(deleted)

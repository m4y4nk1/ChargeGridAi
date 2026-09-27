"""Data catalogue: sources, licences, and snapshot freshness (Section 11, Section 12.1 `/data`)."""

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import get_settings
from app.db.models.provenance import ApiUsage, DatasetSnapshot, DataSource
from app.db.session import get_session
from app.ingestion.provenance import load_source_registry

router = APIRouter(prefix="/data-sources", tags=["data-sources"])


class SnapshotOut(BaseModel):
    id: str
    retrieved_at: datetime
    period_start: str | None
    period_end: str | None
    granularity: str | None
    row_count: int | None
    stored: bool
    checksum: str | None
    quality_report: dict[str, Any] | None


class DataSourceOut(BaseModel):
    id: str
    name: str
    license_class: str
    license_text: str | None
    attribution: str | None
    terms_url: str | None
    refresh_cadence: str | None
    # Ingested sources: fresh | stale | never | n/a. Live sources (fetched on use, never
    # stored, e.g. Google under its terms): live | off | not_configured.
    freshness: str
    access: str = "ingested"  # ingested | live
    usage: dict[str, Any] | None = None  # live sources: calls and cost this month
    # Latest snapshot of each dataset from this source (e.g. OSM has the full
    # map import and the derived charger extract), newest first.
    datasets: list[SnapshotOut]


def _snapshot_out(s: DatasetSnapshot) -> SnapshotOut:
    return SnapshotOut(
        id=str(s.id),
        retrieved_at=s.retrieved_at,
        period_start=s.period_start.isoformat() if s.period_start else None,
        period_end=s.period_end.isoformat() if s.period_end else None,
        granularity=s.granularity,
        row_count=s.row_count,
        stored=s.storage_uri is not None,
        checksum=s.checksum,
        quality_report=s.quality_report,
    )


def _freshness(latest: DatasetSnapshot | None, stale_after_days: int | None) -> str:
    if latest is None:
        return "never"
    if stale_after_days is None:
        return "n/a"
    age = datetime.now(UTC) - latest.retrieved_at
    return "stale" if age > timedelta(days=stale_after_days) else "fresh"


def _live_status(source_id: str) -> str:
    """Whether a live (never-ingested) source is usable right now."""
    import os

    st = get_settings()
    configured = {
        "google_maps": bool(os.environ.get("VITE_GOOGLE_MAPS_BROWSER_KEY")),
        "google_places": bool(st.google_maps_server_key),
        "google_solar": bool(st.google_maps_server_key),
        "mappls": bool(st.mappls_client_id),
    }.get(source_id, False)
    if not configured:
        return "not_configured"
    if source_id == "google_solar" and not st.google_solar_enabled:
        return "off"
    if source_id == "mappls":
        return "off"  # adapter deliberately stubbed pending a terms review
    return "live"


async def _usage(session: AsyncSession) -> dict[str, dict[str, Any]]:
    month = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    rows = (
        await session.execute(
            select(
                ApiUsage.provider,
                func.coalesce(func.sum(ApiUsage.units), 0),
                func.coalesce(func.sum(ApiUsage.cost_inr), 0),
                func.max(ApiUsage.ts),
            )
            .where(ApiUsage.ts >= month)
            .group_by(ApiUsage.provider)
        )
    ).all()
    last_ever: dict[str, Any] = {
        p: last
        for p, last in (
            await session.execute(
                select(ApiUsage.provider, func.max(ApiUsage.ts)).group_by(ApiUsage.provider)
            )
        ).all()
    }
    out = {
        p: {"calls_this_month": int(n), "cost_inr_this_month": float(c), "last_used": last}
        for p, n, c, last in rows
    }
    for p, last in last_ever.items():
        out.setdefault(p, {"calls_this_month": 0, "cost_inr_this_month": 0.0, "last_used": last})
    return out


@router.get("", response_model=list[DataSourceOut])
async def list_data_sources(session: AsyncSession = Depends(get_session)) -> list[DataSourceOut]:
    registry = load_source_registry()
    sources = (await session.execute(select(DataSource).order_by(DataSource.id))).scalars().all()
    latest: dict[str, dict[str | None, DatasetSnapshot]] = {}
    for snap in (
        await session.execute(select(DatasetSnapshot).order_by(DatasetSnapshot.created_at))
    ).scalars():
        latest.setdefault(snap.source_id, {})[snap.granularity] = snap

    usage = await _usage(session)
    out = []
    for s in sources:
        datasets = sorted(latest.get(s.id, {}).values(), key=lambda d: d.retrieved_at, reverse=True)
        if registry.get(s.id, {}).get("access") == "live":
            out.append(
                DataSourceOut(
                    id=s.id,
                    name=s.name,
                    license_class=s.license_class,
                    license_text=s.license_text,
                    attribution=s.attribution,
                    terms_url=s.terms_url,
                    refresh_cadence=s.refresh_cadence,
                    freshness=_live_status(s.id),
                    access="live",
                    usage=usage.get(s.id)
                    or {"calls_this_month": 0, "cost_inr_this_month": 0.0, "last_used": None},
                    datasets=[],
                )
            )
            continue
        out.append(
            DataSourceOut(
                id=s.id,
                name=s.name,
                license_class=s.license_class,
                license_text=s.license_text,
                attribution=s.attribution,
                terms_url=s.terms_url,
                refresh_cadence=s.refresh_cadence,
                freshness=_freshness(
                    datasets[0] if datasets else None,
                    registry.get(s.id, {}).get("stale_after_days"),
                ),
                datasets=[_snapshot_out(d) for d in datasets],
            )
        )
    return out


@router.get("/{source_id}/snapshots", response_model=list[SnapshotOut])
async def list_snapshots(
    source_id: str, session: AsyncSession = Depends(get_session)
) -> list[SnapshotOut]:
    if await session.get(DataSource, source_id) is None:
        raise HTTPException(404, "Unknown data source")
    snaps = (
        (
            await session.execute(
                select(DatasetSnapshot)
                .where(DatasetSnapshot.source_id == source_id)
                .order_by(DatasetSnapshot.retrieved_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [_snapshot_out(s) for s in snaps]

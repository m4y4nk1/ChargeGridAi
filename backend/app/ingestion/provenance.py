"""Source registry, dataset snapshots, and quality reports (Sections 8.1, 13).

Every ingestion run produces exactly one `dataset_snapshot`: when it ran,
what it covers, a checksum of the raw input, where that raw input is kept
(only if the licence allows storing it), and a quality report — row
counts, null rates, bbox coverage, and drift against the previous snapshot.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime
from typing import Any

import boto3
import yaml
from botocore.exceptions import ClientError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import get_settings
from app.db.models.provenance import DatasetSnapshot, DataSource

# Restricted content must never be warehoused (Section 2 principle 3).
_STORABLE_LICENSES = {"OPEN", "GOVERNMENT", "SYNTHETIC"}


def load_source_registry() -> dict[str, dict[str, Any]]:
    path = get_settings().config_dir / "data_sources.yaml"
    sources: dict[str, dict[str, Any]] = yaml.safe_load(path.read_text())["sources"]
    # Each OCPI operator with a data agreement is its own source ("operator:<id>").
    operators = get_settings().config_dir / "ocpi_operators.yaml"
    if operators.exists():
        for op_id, op in (yaml.safe_load(operators.read_text()).get("operators") or {}).items():
            sources[f"operator:{op_id}"] = {
                "name": f"{op['name']} (OCPI 2.2.1 feed)",
                "license_class": op.get("license_class", "RESTRICTED_COMMERCIAL"),
                "license_text": "Operator data agreement",
                "attribution": op["name"],
                "terms_url": None,
                "refresh_cadence": "daily",
                "stale_after_days": 3,
            }
    return sources


async def sync_data_sources(session: AsyncSession) -> None:
    for source_id, meta in load_source_registry().items():
        values = {
            "id": source_id,
            "name": meta["name"],
            "license_class": meta["license_class"],
            "license_text": meta.get("license_text"),
            "attribution": meta.get("attribution"),
            "terms_url": meta.get("terms_url"),
            "refresh_cadence": meta.get("refresh_cadence"),
        }
        stmt = insert(DataSource).values(**values)
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=[DataSource.id], set_={k: v for k, v in values.items() if k != "id"}
            )
        )
    await session.commit()


def store_raw(source_id: str, license_class: str, filename: str, payload: bytes) -> str | None:
    """Write the raw input to the immutable raw lake; returns its URI, or None if not storable."""
    if license_class not in _STORABLE_LICENSES:
        return None
    settings = get_settings()
    s3 = boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
    )
    try:
        s3.head_bucket(Bucket=settings.s3_bucket)
    except ClientError:
        s3.create_bucket(Bucket=settings.s3_bucket)
    key = f"{source_id}/{datetime.now(UTC):%Y/%m/%d/%H%M%S}-{filename}"
    s3.put_object(Bucket=settings.s3_bucket, Key=key, Body=payload)
    return f"s3://{settings.s3_bucket}/{key}"


def quality_report(
    records: Sequence[Any],
    fields: Sequence[str],
    coordinate: Callable[[Any], tuple[float, float] | None] | None = None,
    previous_row_count: int | None = None,
    **extra: Any,
) -> dict[str, Any]:
    n = len(records)
    report: dict[str, Any] = {"row_count": n}
    if n:
        report["null_rate"] = {
            f: round(sum(1 for r in records if _get(r, f) in (None, "", [])) / n, 4) for f in fields
        }
    if coordinate is not None:
        coords = [c for r in records if (c := coordinate(r)) is not None]
        if coords:
            lats, lngs = [c[0] for c in coords], [c[1] for c in coords]
            report["bbox"] = [min(lngs), min(lats), max(lngs), max(lats)]
    if previous_row_count is not None:
        report["previous_row_count"] = previous_row_count
        report["row_count_drift"] = (
            round((n - previous_row_count) / previous_row_count, 4) if previous_row_count else None
        )
    report.update(extra)
    return report


def _get(record: Any, field: str) -> Any:
    return record.get(field) if isinstance(record, dict) else getattr(record, field, None)


async def previous_row_count(session: AsyncSession, source_id: str, granularity: str) -> int | None:
    """Row count of the previous snapshot of the *same* dataset (source + granularity)."""
    row_count: int | None = await session.scalar(
        select(DatasetSnapshot.row_count)
        .where(DatasetSnapshot.source_id == source_id, DatasetSnapshot.granularity == granularity)
        .order_by(DatasetSnapshot.retrieved_at.desc())
        .limit(1)
    )
    return row_count


async def record_snapshot(
    session: AsyncSession,
    *,
    source_id: str,
    retrieved_at: datetime,
    raw: bytes,
    raw_filename: str,
    row_count: int,
    granularity: str,
    report: dict[str, Any],
    period: tuple[date, date] | None = None,
) -> DatasetSnapshot:
    license_class = load_source_registry()[source_id]["license_class"]
    snapshot = DatasetSnapshot(
        source_id=source_id,
        retrieved_at=retrieved_at,
        period_start=period[0] if period else None,
        period_end=period[1] if period else None,
        granularity=granularity,
        row_count=row_count,
        storage_uri=store_raw(source_id, license_class, raw_filename, raw),
        checksum="sha256:" + hashlib.sha256(raw).hexdigest(),
        quality_report=json.loads(json.dumps(report, default=str)),
    )
    session.add(snapshot)
    await session.flush()
    return snapshot

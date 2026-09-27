"""DISCOM asset file -> grid_asset_rated (Section 7.1 row 22).

CSV columns (template: data/templates/discom_assets_template.csv):
  asset_code, asset_type (transformer | substation), name, lat, lng, voltage_kv,
  rated_kva, base_peak_kva, measured_on (YYYY-MM-DD), loading_limit (0-1, optional),
  h00..h23 (optional hourly base load in kVA; all 24 or none)

Loading a file replaces that DISCOM's assets. The file is contractual
(RESTRICTED_COMMERCIAL), so only its checksum and quality report are recorded, never
the file itself.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.regions import all_regions
from app.db.models.grid import GridAssetRated

ASSET_TYPES = ("transformer", "substation")
HOUR_COLUMNS = [f"h{h:02d}" for h in range(24)]
REQUIRED = ("asset_code", "asset_type", "lat", "lng", "rated_kva", "base_peak_kva")


@dataclass
class ParsedFile:
    rows: list[dict[str, Any]] = field(default_factory=list)
    rejected: list[dict[str, Any]] = field(default_factory=list)


def _num(v: str | None) -> float | None:
    if v is None or not v.strip():
        return None
    return float(v.replace(",", ""))


def parse_csv(text: str, discom: str) -> ParsedFile:
    out = ParsedFile()
    reader = csv.DictReader(io.StringIO(text))
    missing = [c for c in REQUIRED if c not in (reader.fieldnames or [])]
    if missing:
        raise ValueError(f"missing columns: {missing}")
    boxes = [r.bbox for r in all_regions()]
    seen: set[str] = set()
    for line, raw in enumerate(reader, start=2):
        try:
            code = (raw.get("asset_code") or "").strip()
            kind = (raw.get("asset_type") or "").strip().lower()
            lat, lng = _num(raw.get("lat")), _num(raw.get("lng"))
            rated, base = _num(raw.get("rated_kva")), _num(raw.get("base_peak_kva"))
            limit = _num(raw.get("loading_limit"))
            hours = [_num(raw.get(c)) for c in HOUR_COLUMNS]
            problems = []
            if not code:
                problems.append("no asset_code")
            elif code in seen:
                problems.append("duplicate asset_code")
            if kind not in ASSET_TYPES:
                problems.append(f"asset_type must be one of {ASSET_TYPES}")
            if lat is None or lng is None or not any(b.contains(lat, lng) for b in boxes):
                problems.append("coordinates missing or outside every modelled region")
            if rated is None or rated <= 0:
                problems.append("rated_kva must be > 0")
            if base is None or base < 0:
                problems.append("base_peak_kva must be >= 0")
            if limit is not None and not 0 < limit <= 1:
                problems.append("loading_limit must be in (0, 1]")
            filled = [h for h in hours if h is not None]
            if filled and len(filled) != 24:
                problems.append("hourly columns must be all 24 or none")
            if any(h < 0 for h in filled):
                problems.append("hourly load must be >= 0")
            measured = raw.get("measured_on", "").strip()
            measured_on = date.fromisoformat(measured) if measured else None
        except ValueError as exc:
            problems = [str(exc)]
        if problems:
            out.rejected.append(
                {"line": line, "asset_code": raw.get("asset_code"), "problems": problems}
            )
            continue
        seen.add(code)
        assert lat is not None and lng is not None and rated is not None and base is not None
        out.rows.append(
            {
                "id": f"{discom}:{code}",
                "discom": discom,
                "asset_type": kind,
                "name": (raw.get("name") or "").strip() or None,
                "voltage_kv": _num(raw.get("voltage_kv")),
                "rated_kva": rated,
                "loading_limit": limit,
                "base_peak_kva": max(filled) if len(filled) == 24 else base,
                "base_hourly_kva": filled if len(filled) == 24 else None,
                "measured_on": measured_on,
                "geom": f"SRID=4326;POINT({lng} {lat})",
            }
        )
    return out


async def ingest_discom(
    session: AsyncSession, raw: bytes, filename: str, discom: str
) -> dict[str, Any]:
    from app.ingestion.provenance import quality_report, record_snapshot, sync_data_sources

    await sync_data_sources(session)
    parsed = parse_csv(raw.decode("utf-8-sig"), discom)
    dates = [r["measured_on"] for r in parsed.rows if r["measured_on"]]
    report = quality_report(
        [],
        (),
        previous_row_count=None,
        discom=discom,
        rejected=parsed.rejected[:200],
        rejected_count=len(parsed.rejected),
        transformers=sum(r["asset_type"] == "transformer" for r in parsed.rows),
        substations=sum(r["asset_type"] == "substation" for r in parsed.rows),
        with_hourly_profile=sum(r["base_hourly_kva"] is not None for r in parsed.rows),
        measured_range=[str(min(dates)), str(max(dates))] if dates else None,
    )
    report["row_count"] = len(parsed.rows)
    snapshot = await record_snapshot(
        session,
        source_id="discom",
        retrieved_at=datetime.now(UTC),
        raw=raw,  # checksummed only: RESTRICTED_COMMERCIAL is never stored
        raw_filename=filename,
        row_count=len(parsed.rows),
        granularity="asset",
        report=report,
        period=(min(dates), max(dates)) if dates else None,
    )
    await session.execute(delete(GridAssetRated).where(GridAssetRated.discom == discom))
    if parsed.rows:
        await session.execute(
            insert(GridAssetRated),
            [
                {
                    **r,
                    "snapshot_id": snapshot.id,
                    "license_class": "RESTRICTED_COMMERCIAL",
                    "extra": {},
                }
                for r in parsed.rows
            ],
        )
    await session.commit()
    return {
        "discom": discom,
        "snapshot_id": str(snapshot.id),
        "loaded": len(parsed.rows),
        "rejected": len(parsed.rejected),
    }

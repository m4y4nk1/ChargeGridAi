import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.provenance import DatasetSnapshot, DataSource, Evidence
from app.db.session import get_session

router = APIRouter(tags=["evidence"])


class EvidenceSource(BaseModel):
    snapshot_id: str
    source_id: str
    source_name: str
    license_class: str
    attribution: str | None
    retrieved_at: datetime


class EvidenceOut(BaseModel):
    id: str
    subject_type: str
    subject_id: str
    metric: str
    value_num: float | None
    value_text: str | None
    unit: str | None
    p10: float | None
    p90: float | None
    method: str | None
    confidence: str
    created_at: datetime
    sources: list[EvidenceSource]


@router.get("/evidence", response_model=list[EvidenceOut])
async def get_evidence(
    ids: str = Query(..., description="Comma-separated evidence IDs"),
    session: AsyncSession = Depends(get_session),
) -> list[EvidenceOut]:
    try:
        wanted = [uuid.UUID(i) for i in ids.split(",") if i.strip()]
    except ValueError as exc:
        raise HTTPException(422, "ids must be UUIDs") from exc

    rows = (await session.execute(select(Evidence).where(Evidence.id.in_(wanted)))).scalars().all()
    snapshot_ids = {sid for e in rows for sid in e.snapshot_ids}
    snapshots = {
        snap.id: (snap, source)
        for snap, source in (
            await session.execute(
                select(DatasetSnapshot, DataSource)
                .join(DataSource, DataSource.id == DatasetSnapshot.source_id)
                .where(DatasetSnapshot.id.in_(snapshot_ids))
            )
        ).all()
    }
    return [
        EvidenceOut(
            id=str(e.id),
            subject_type=e.subject_type,
            subject_id=e.subject_id,
            metric=e.metric,
            value_num=e.value_num,
            value_text=e.value_text,
            unit=e.unit,
            p10=e.p10,
            p90=e.p90,
            method=e.method,
            confidence=e.confidence,
            created_at=e.created_at,
            sources=[
                EvidenceSource(
                    snapshot_id=str(snap.id),
                    source_id=source.id,
                    source_name=source.name,
                    license_class=source.license_class,
                    attribution=source.attribution,
                    retrieved_at=snap.retrieved_at,
                )
                for sid in e.snapshot_ids
                if sid in snapshots
                for snap, source in [snapshots[sid]]
            ],
        )
        for e in rows
    ]

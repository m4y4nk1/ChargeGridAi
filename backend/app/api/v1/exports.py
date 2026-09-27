"""GET /exports/runs/{run_id}.{geojson|xlsx|pdf}: licence-filtered plan exports with an
attribution sheet (Section 11). Audited."""

from __future__ import annotations

import re
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Principal, audit, get_principal
from app.db.session import get_session
from app.services.exports import ExportError, plan_data, to_geojson, to_pdf, to_xlsx

router = APIRouter(prefix="/exports", tags=["exports"])

MEDIA = {
    "geojson": "application/geo+json",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pdf": "application/pdf",
}


@router.get("/runs/{run_id}.{fmt}")
async def export_run(
    run_id: uuid.UUID,
    fmt: Literal["geojson", "xlsx", "pdf"],
    request: Request,
    optimisation_id: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> Response:
    try:
        data = await plan_data(session, run_id, optimisation_id)
    except ExportError as exc:
        raise HTTPException(404 if "not found" in str(exc) else 409, str(exc)) from exc
    body = {"geojson": to_geojson, "xlsx": to_xlsx, "pdf": to_pdf}[fmt](data)
    await audit(
        session,
        principal,
        f"export.{fmt}",
        "planning_run",
        str(run_id),
        {"optimisation_id": str(data["optimisation"].id), "sites": len(data["sites"])},
        request,
    )
    await session.commit()
    slug = re.sub(r"[^a-z0-9]+", "-", data["scenario"].name.lower()).strip("-")[:60] or "plan"
    return Response(
        body,
        media_type=MEDIA[fmt],
        headers={"Content-Disposition": f'attachment; filename="chargegrid-{slug}.{fmt}"'},
    )

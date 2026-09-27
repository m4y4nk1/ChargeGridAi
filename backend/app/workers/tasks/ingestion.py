"""Celery wrappers around the ingestion jobs (Section 13).

Each task runs the same coroutine the CLI does, so scheduled and manual runs
are identical. Charger ingestions chain straight into fusion.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import task_session
from app.ingestion import pipelines
from app.ingestion.provenance import sync_data_sources
from app.services.demand_run import run_demand
from app.workers.celery_app import celery_app


def _run(job: Callable[[AsyncSession], Awaitable[pipelines.JobSummary]]) -> dict[str, Any]:
    async def go() -> dict[str, Any]:
        async with task_session() as session:
            await sync_data_sources(session)
            summary = await job(session)
            fused = (
                await pipelines.run_charger_fusion(session)
                if summary.source_id in {"osm", "ocm", "bee_evyatra"}
                else None
            )
            return {"job": asdict(summary), "fusion": asdict(fused) if fused else None}

    return asyncio.run(go())


@celery_app.task(name="ingest.osm_chargers")
def ingest_osm_chargers() -> dict[str, Any]:
    return _run(pipelines.ingest_osm_chargers)


@celery_app.task(name="ingest.ocm")
def ingest_ocm() -> dict[str, Any]:
    return _run(pipelines.ingest_ocm)


@celery_app.task(name="demand.run")
def run_demand_task(multiplier: float = 1.0) -> dict[str, Any]:
    """Section 13 `rebuild_features`: re-run demand + gap after any upstream snapshot."""

    async def go() -> dict[str, Any]:
        async with task_session() as session:
            summary = await run_demand(session, multiplier=multiplier)
            return {"run_id": summary.run_id, "scenarios": summary.scenarios}

    return asyncio.run(go())


@celery_app.task(name="ingest.ocpi")
def ingest_ocpi(operator_id: str) -> dict[str, Any]:
    """One operator's OCPI feed (fusion runs inside the job)."""
    return _run(lambda session: pipelines.ingest_ocpi(session, operator_id))


@celery_app.task(name="ingest.calibrate")
def calibrate_task() -> dict[str, Any]:
    from app.services.calibration import calibrate

    async def go() -> dict[str, Any]:
        async with task_session() as session:
            return await calibrate(session)

    return asyncio.run(go())

"""Planning-run Celery task (Section 5 request flow). Phase 4 runs
resolve_data -> candidates -> feasibility; later phases add their steps to
the same service."""

import asyncio
import uuid
from datetime import UTC, datetime

from app.db.models.planning import PlanningRun
from app.db.session import task_session
from app.services.candidate_run import continue_optimisation, execute_run
from app.workers.celery_app import celery_app


async def _mark_failed_if_never_started(run_id: uuid.UUID, error: str) -> None:
    """A task that dies before execute_run takes over must not leave the run 'queued'."""
    async with task_session() as session:
        run = await session.get(PlanningRun, run_id)
        if run is not None and run.status in ("queued", "running"):
            run.status, run.finished_at, run.error = "failed", datetime.now(UTC), error
            await session.commit()


@celery_app.task(name="run.plan")
def plan_run(run_id: str) -> str:
    rid = uuid.UUID(run_id)

    async def go() -> str:
        try:
            async with task_session() as session:
                run = await execute_run(session, rid)
                return run.status
        except Exception as exc:
            await _mark_failed_if_never_started(rid, f"{type(exc).__name__}: {exc}")
            raise

    return asyncio.run(go())


@celery_app.task(name="run.optimise")
def optimise_run_task(run_id: str) -> str:
    """Continue a run stopped after scoring, once the user has confirmed."""
    rid = uuid.UUID(run_id)

    async def go() -> str:
        async with task_session() as session:
            run = await continue_optimisation(session, rid)
            return run.status

    return asyncio.run(go())

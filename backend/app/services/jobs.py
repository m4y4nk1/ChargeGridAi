"""Where planning runs execute: Celery (default) or Temporal (JOB_RUNNER=temporal).

Task ids are Celery task ids, or "temporal:<workflow id>" for Temporal runs, so a
run started under one runner is always continued and reconciled by the same one.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.core.settings import get_settings

TEMPORAL = "temporal:"


async def _temporal() -> Any:
    from temporalio.client import Client

    return await Client.connect(get_settings().temporal_address)


async def start_planning(run_id: uuid.UUID) -> str:
    if get_settings().job_runner == "temporal":
        from app.workflows.planning import TASK_QUEUE, PlanningWorkflow

        client = await _temporal()
        handle = await client.start_workflow(
            PlanningWorkflow.run, str(run_id), id=f"run-{run_id}", task_queue=TASK_QUEUE
        )
        return TEMPORAL + str(handle.id)
    from app.workers.tasks.planning import plan_run

    return str(plan_run.delay(str(run_id)).id)


async def continue_planning(run: Any) -> str:
    """Resume a run stopped after scoring: a signal for Temporal, a new task for Celery."""
    if run.task_id and run.task_id.startswith(TEMPORAL):
        from app.workflows.planning import PlanningWorkflow

        client = await _temporal()
        handle = client.get_workflow_handle(run.task_id[len(TEMPORAL) :])
        await handle.signal(PlanningWorkflow.confirm)
        return str(run.task_id)
    from app.workers.tasks.planning import optimise_run_task

    return str(optimise_run_task.delay(str(run.id)).id)


async def failure_reason(task_id: str) -> str | None:
    """Why the job behind a run died (worker killed, workflow failed), or None."""
    if task_id.startswith(TEMPORAL):
        from temporalio.client import WorkflowExecutionStatus

        try:
            client = await _temporal()
            desc = await client.get_workflow_handle(task_id[len(TEMPORAL) :]).describe()
        except Exception:
            return None
        bad = {
            WorkflowExecutionStatus.FAILED: "workflow failed",
            WorkflowExecutionStatus.TERMINATED: "workflow terminated",
            WorkflowExecutionStatus.TIMED_OUT: "workflow timed out",
            WorkflowExecutionStatus.CANCELED: "workflow cancelled",
        }
        return bad.get(desc.status) if desc.status else None
    from celery.result import AsyncResult

    from app.workers.celery_app import celery_app

    result = AsyncResult(task_id, app=celery_app)
    if result.state != "FAILURE":
        return None
    exc = result.result
    return f"{type(exc).__name__}: {exc}" if isinstance(exc, BaseException) else str(exc)

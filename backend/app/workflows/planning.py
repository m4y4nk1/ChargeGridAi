"""Planning run as a Temporal workflow (Section 4: migrate long multi-step workflows to
Temporal in Phase 11).

    plan_to_scoring  ->  [wait for the `confirm` signal if the run stops after scoring]
                     ->  optimise_and_value

Each step is an activity with its own retry policy and heartbeat, so a worker dying
mid-run resumes from the last completed step instead of failing the run. The
human-in-the-loop confirmation (agents, Section 10.1) is a durable signal rather than
a flag polled in the database.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from temporalio import activity, workflow
from temporalio.common import RetryPolicy

TASK_QUEUE = "planning"
CONFIRM_TIMEOUT = timedelta(days=7)
_RETRY = RetryPolicy(
    maximum_attempts=3,
    initial_interval=timedelta(seconds=10),
    non_retryable_error_types=["RunError"],
)


@activity.defn
async def plan_to_scoring(run_id: str) -> dict[str, Any]:
    from app.db.session import task_session
    from app.services.candidate_run import execute_run

    activity.heartbeat("starting")
    async with task_session() as session:
        run = await execute_run(session, uuid.UUID(run_id))
        return {
            "status": run.status,
            "awaiting_confirmation": bool(
                (run.funnel.get("optimisation") or {}).get("awaiting_confirmation")
            ),
        }


@activity.defn
async def optimise_and_value(run_id: str) -> dict[str, Any]:
    from app.db.session import task_session
    from app.services.candidate_run import continue_optimisation

    activity.heartbeat("starting")
    async with task_session() as session:
        run = await continue_optimisation(session, uuid.UUID(run_id))
        return {"status": run.status}


@workflow.defn
class PlanningWorkflow:
    def __init__(self) -> None:
        self.confirmed = False

    @workflow.signal
    def confirm(self) -> None:
        self.confirmed = True

    @workflow.query
    def waiting_for_confirmation(self) -> bool:
        return not self.confirmed

    @workflow.run
    async def run(self, run_id: str) -> dict[str, Any]:
        first: dict[str, Any] = await workflow.execute_activity(
            plan_to_scoring,
            run_id,
            start_to_close_timeout=timedelta(minutes=30),
            heartbeat_timeout=timedelta(minutes=10),
            retry_policy=_RETRY,
        )
        if first["status"] != "succeeded" or not first["awaiting_confirmation"]:
            return first
        await workflow.wait_condition(lambda: self.confirmed, timeout=CONFIRM_TIMEOUT)
        second: dict[str, Any] = await workflow.execute_activity(
            optimise_and_value,
            run_id,
            start_to_close_timeout=timedelta(minutes=30),
            heartbeat_timeout=timedelta(minutes=10),
            retry_policy=_RETRY,
        )
        return second

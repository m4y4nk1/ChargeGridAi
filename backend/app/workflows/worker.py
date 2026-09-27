"""Temporal worker: `python -m app.workflows.worker` (compose profile `temporal`)."""

from __future__ import annotations

import asyncio

from temporalio.client import Client
from temporalio.worker import Worker

from app.core.settings import get_settings
from app.workflows.planning import (
    TASK_QUEUE,
    PlanningWorkflow,
    optimise_and_value,
    plan_to_scoring,
)


async def main() -> None:
    client = await Client.connect(get_settings().temporal_address)
    # One activity at a time: a planning run needs most of the worker's memory.
    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[PlanningWorkflow],
        activities=[plan_to_scoring, optimise_and_value],
        max_concurrent_activities=1,
    )
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())

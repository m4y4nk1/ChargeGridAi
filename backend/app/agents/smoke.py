"""End-to-end smoke run of the agent graph without a model key:

    python -m app.agents.smoke            # scripted model, real tools, real planning run

Runs the Section 10.5 example (20 fast-charging sites along Mumbai-Pune, ₹30 crore)
through every node, including the confirmation pause, and prints the event stream and
the validated report. Uses the Celery worker for the planning run.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time

from sqlalchemy import select

from app.agents import session as runner
from app.agents.scripted import ScriptedFlow, corridor_plan
from app.db.models.agents import AgentEvent, AgentSession
from app.db.session import async_session_factory

REQUEST = "Find 20 fast-charging locations along Mumbai–Pune with a ₹30 crore budget"


async def _wait(session_id: object, statuses: tuple[str, ...], seen: list[int]) -> str:
    while True:
        async with async_session_factory() as db:
            s = await db.get(AgentSession, session_id)
            assert s is not None
            events = (
                (
                    await db.execute(
                        select(AgentEvent)
                        .where(AgentEvent.session_id == session_id, AgentEvent.id > seen[0])
                        .order_by(AgentEvent.id)
                    )
                )
                .scalars()
                .all()
            )
        for e in events:
            seen[0] = e.id
            if e.type != "partial_text":
                brief = {k: v for k, v in e.data.items() if k not in ("report", "findings")}
                print(f"[{e.type}] {e.agent or '-'} {json.dumps(brief, default=str)[:220]}")
        if s.status in statuses and not runner.is_running(s.id):
            return s.status
        await asyncio.sleep(1.0)


async def main() -> int:
    flow = ScriptedFlow(corridor_plan())
    runner.backend_factory = flow.backend
    async with async_session_factory() as db:
        s = AgentSession(request=REQUEST, status="running", summary={})
        db.add(s)
        await db.commit()
        sid = s.id
    started = time.monotonic()
    seen = [0]
    runner.launch(sid, runner.start_input(REQUEST, None, None))
    status = await _wait(sid, ("awaiting_confirmation", "completed", "failed", "stopped"), seen)
    if status == "awaiting_confirmation":
        print("--- confirming ---")
        runner.launch(sid, runner.resume_input(True, None))
        await asyncio.sleep(0.5)
        status = await _wait(sid, ("completed", "failed", "stopped"), seen)
    async with async_session_factory() as db:
        final = await db.get(AgentSession, sid)
        assert final is not None
    s = final
    print(f"--- {status} in {time.monotonic() - started:.0f}s, session {sid} ---")
    report = (s.summary or {}).get("report") or {}
    print(report.get("markdown", s.error))
    print(json.dumps(report.get("validation"), indent=1))
    return (
        0
        if status == "completed" and report.get("validation", {}).get("unmatched_after") == 0
        else 1
    )


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

"""Runs agent sessions: drives the graph, persists SSE events (agent_event) and model-call
traces (agent_llm_call), keeps session totals, and pauses/resumes at the confirmation.

Sessions run as asyncio tasks in the API process. They are I/O-bound (model calls,
waiting on planning runs that execute in the Celery worker), and the worker's two slots
are kept for planning runs. The graph state is checkpointed in Postgres after every
node, so an API restart loses at most the node in flight; such a session is marked
`interrupted` and can be resumed.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.types import Command
from sqlalchemy import update

from app.agents.graph import Deps, build_graph
from app.agents.llm.client import (
    LLM,
    AgentLimitExceeded,
    AnthropicBackend,
    Budget,
    LLMNotConfigured,
    LLMRefused,
    ModelBackend,
    agents_config,
)
from app.core.settings import get_settings
from app.db.models.agents import AgentEvent, AgentLLMCall, AgentSession
from app.db.session import async_session_factory

log = logging.getLogger(__name__)

_TASKS: dict[uuid.UUID, asyncio.Task[None]] = {}
_SETUP_DONE = False


def _default_backend() -> ModelBackend:
    settings = get_settings()
    if settings.llm_provider != "anthropic":
        raise LLMNotConfigured(
            f"LLM_PROVIDER={settings.llm_provider} has no adapter; only anthropic is built"
        )
    return AnthropicBackend(settings.anthropic_api_key)


# Tests and offline evals replace this with a ScriptedBackend factory.
backend_factory: Callable[[], ModelBackend] = _default_backend


def llm_configured() -> bool:
    settings = get_settings()
    return backend_factory is not _default_backend or (
        settings.llm_provider == "anthropic" and bool(settings.anthropic_api_key)
    )


def is_running(session_id: uuid.UUID) -> bool:
    task = _TASKS.get(session_id)
    return task is not None and not task.done()


def _checkpoint_url() -> str:
    return get_settings().database_url.replace("postgresql+asyncpg://", "postgresql://")


async def emit(session_id: uuid.UUID, type_: str, agent: str | None, data: dict[str, Any]) -> None:
    async with async_session_factory() as db:
        db.add(AgentEvent(session_id=session_id, type=type_, agent=agent, data=data))
        await db.commit()


async def _trace(session_id: uuid.UUID, call: dict[str, Any]) -> None:
    async with async_session_factory() as db:
        db.add(AgentLLMCall(session_id=session_id, **call))
        await db.execute(
            update(AgentSession)
            .where(AgentSession.id == session_id)
            .values(
                cost_usd=AgentSession.cost_usd + call["cost_usd"],
                input_tokens=AgentSession.input_tokens + call["input_tokens"],
                output_tokens=AgentSession.output_tokens + call["output_tokens"],
                llm_calls=AgentSession.llm_calls + 1,
            )
        )
        await db.commit()


async def _set(session_id: uuid.UUID, **values: Any) -> None:
    async with async_session_factory() as db:
        await db.execute(
            update(AgentSession)
            .where(AgentSession.id == session_id)
            .values(updated_at=datetime.now(UTC), **values)
        )
        await db.commit()


def _budget(usage: dict[str, Any] | None) -> Budget:
    limits = agents_config()["limits"]
    u = usage or {}
    return Budget(
        max_usd=float(limits["max_usd_per_session"]),
        max_llm_calls=int(limits["max_llm_calls"]),
        max_tool_calls=int(limits["max_tool_calls"]),
        usd=float(u.get("usd", 0.0)),
        llm_calls=int(u.get("llm_calls", 0)),
        tool_calls=int(u.get("tool_calls", 0)),
        tool_calls_by_agent=dict(u.get("tool_calls_by_agent", {})),
    )


def launch(session_id: uuid.UUID, graph_input: dict[str, Any] | Command[Any] | None) -> None:
    task = asyncio.create_task(_drive(session_id, graph_input))
    _TASKS[session_id] = task
    task.add_done_callback(lambda _t: _TASKS.pop(session_id, None))


def cancel(session_id: uuid.UUID) -> bool:
    task = _TASKS.get(session_id)
    if task is None or task.done():
        return False
    task.cancel()
    return True


async def _drive(session_id: uuid.UUID, graph_input: dict[str, Any] | Command[Any] | None) -> None:
    global _SETUP_DONE
    from app.main import app  # the platform API, called in-process by the tools

    config: RunnableConfig = {"configurable": {"thread_id": str(session_id)}, "recursion_limit": 60}
    await _set(session_id, status="running", error=None)
    try:
        async with (
            AsyncPostgresSaver.from_conn_string(_checkpoint_url()) as saver,
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://chargegrid.internal/api/v1",
                timeout=None,
                headers=await _acting_as(session_id),
            ) as api,
        ):
            if not _SETUP_DONE:
                await saver.setup()
                _SETUP_DONE = True
            graph = build_graph().compile(checkpointer=saver)
            previous = await graph.aget_state(config)
            usage = (previous.values or {}).get("usage") if previous else None

            async def _emit(type_: str, agent: str | None, data: dict[str, Any]) -> None:
                await emit(session_id, type_, agent, data)

            async def _sink(call: dict[str, Any]) -> None:
                await _trace(session_id, call)

            llm = LLM(backend_factory(), _budget(usage), _sink)
            deps = Deps(
                session_id=session_id, llm=llm, api=api, db=async_session_factory, emit=_emit
            )
            try:
                result = await graph.ainvoke(graph_input, config, context=deps)  # type: ignore[arg-type]
            finally:
                await _set(session_id, tool_calls=llm.budget.tool_calls)
            interrupts = result.get("__interrupt__") if isinstance(result, dict) else None
            if interrupts:
                payload = interrupts[0].value
                await _set(
                    session_id,
                    status="awaiting_confirmation",
                    run_id=_uuid(result.get("run_id")),
                    scenario_id=_uuid(result.get("scenario_id")),
                    region_id=result.get("region_id"),
                    summary={"plan": result.get("plan"), "confirmation": payload},
                )
                await emit(session_id, "needs_confirmation", "planner", payload)
                return
            report = result.get("report") or {}
            await _set(
                session_id,
                status="completed",
                run_id=_uuid(result.get("run_id")),
                scenario_id=_uuid(result.get("scenario_id")),
                region_id=result.get("region_id"),
                summary={
                    "plan": result.get("plan"),
                    "report": report,
                    "findings": result.get("findings", []),
                },
            )
            await emit(session_id, "completed", "report", {"report": report})
    except asyncio.CancelledError:
        await _set(session_id, status="cancelled")
        await emit(session_id, "error", None, {"message": "Cancelled by the user."})
        raise
    except AgentLimitExceeded as exc:
        msg = (
            f"Stopped early: {exc}. The steps completed so far are kept; ask a narrower "
            "question or raise the limits in config/agents.yaml."
        )
        await _set(session_id, status="stopped", error=msg)
        await emit(session_id, "error", None, {"message": msg, "kind": "limit"})
    except LLMRefused as exc:
        await _set(session_id, status="failed", error=str(exc))
        await emit(session_id, "error", None, {"message": str(exc), "kind": "refusal"})
    except Exception as exc:
        log.exception("agent session %s failed", session_id)
        msg = f"{type(exc).__name__}: {exc}"
        await _set(session_id, status="failed", error=msg)
        await emit(session_id, "error", None, {"message": msg})


async def _acting_as(session_id: uuid.UUID) -> dict[str, str]:
    """Tools call the API as the session's owner (their org and roles); anonymous dev
    sessions call it anonymously."""
    from app.core.auth import mint_local_token
    from app.db.models.org import Org

    async with async_session_factory() as db:
        s = await db.get(AgentSession, session_id)
        if s is None or s.owner_id is None:
            return {}
        org = await db.get(Org, s.org_id) if s.org_id else None
    headers = {"Authorization": f"Bearer {mint_local_token(s.owner_id, minutes=120)}"}
    if org is not None:
        headers["X-Org"] = org.slug
    return headers


def _uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value)) if value else None
    except ValueError:
        return None


def start_input(message: str, region_id: str | None, run_id: str | None) -> dict[str, Any]:
    return {
        "request": message,
        "region_id": region_id,
        "run_id": run_id,
        "plan": None,
        "queue": [],
        "findings": [],
        "report": None,
        "confirmed": False,
    }


def follow_up_input(message: str) -> dict[str, Any]:
    """A new question on the same thread: ids, corridor and facts carry over."""
    return {
        "request": message,
        "plan": None,
        "queue": [],
        "findings": [],
        "report": None,
        "confirmed": False,
    }


def resume_input(approved: bool, note: str | None) -> Command[Any]:
    return Command(resume={"approved": approved, "note": note})

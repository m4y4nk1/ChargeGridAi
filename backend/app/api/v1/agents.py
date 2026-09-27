"""Agent chat (Section 11): POST /agent/sessions, GET /agent/sessions[/{id}],
GET /agent/sessions/{id}/events (SSE), POST .../confirm, POST .../messages,
POST .../cancel, GET .../calls (model-call trace)."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import session as runner
from app.core.auth import Principal, audit, get_principal
from app.core.regions import get_region
from app.db.models.agents import AgentEvent, AgentLLMCall, AgentSession
from app.db.session import async_session_factory, get_session

router = APIRouter(prefix="/agent", tags=["agents"])

TERMINAL = ("completed", "failed", "cancelled", "stopped", "interrupted")


class SessionIn(BaseModel):
    message: str = Field(min_length=3, max_length=4000)
    region_id: str | None = None
    run_id: uuid.UUID | None = Field(default=None, description="Ask about an existing run")


class MessageIn(BaseModel):
    message: str = Field(min_length=3, max_length=4000)


class ConfirmIn(BaseModel):
    approved: bool
    note: str | None = Field(default=None, max_length=1000)


class SessionOut(BaseModel):
    id: str
    status: str
    request: str
    region_id: str | None
    run_id: str | None
    scenario_id: str | None
    summary: dict[str, Any]
    cost_usd: float
    input_tokens: int
    output_tokens: int
    llm_calls: int
    tool_calls: int
    error: str | None
    created_at: datetime
    updated_at: datetime


def _out(s: AgentSession) -> SessionOut:
    return SessionOut(
        id=str(s.id),
        status=s.status,
        request=s.request,
        region_id=s.region_id,
        run_id=str(s.run_id) if s.run_id else None,
        scenario_id=str(s.scenario_id) if s.scenario_id else None,
        summary=s.summary or {},
        cost_usd=round(s.cost_usd, 4),
        input_tokens=s.input_tokens,
        output_tokens=s.output_tokens,
        llm_calls=s.llm_calls,
        tool_calls=s.tool_calls,
        error=s.error,
        created_at=s.created_at,
        updated_at=s.updated_at,
    )


def _require_llm() -> None:
    if not runner.llm_configured():
        raise HTTPException(
            503, "The agents need ANTHROPIC_API_KEY in .env (restart the api service after)"
        )


async def _get(db: AsyncSession, session_id: uuid.UUID) -> AgentSession:
    s = await db.get(AgentSession, session_id)
    if s is None:
        raise HTTPException(404, "Agent session not found")
    if s.status == "running" and not runner.is_running(session_id):
        # The API restarted mid-session; the last checkpoint is kept.
        s.status, s.error = "interrupted", "The server restarted during this session."
        await db.commit()
    return s


@router.post("/sessions", response_model=SessionOut, status_code=202)
async def create_session(
    body: SessionIn,
    request: Request,
    db: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> SessionOut:
    _require_llm()
    if body.region_id:
        try:
            get_region(body.region_id)
        except KeyError as exc:
            raise HTTPException(422, str(exc)) from exc
    s = AgentSession(
        request=body.message,
        region_id=body.region_id,
        run_id=body.run_id,
        status="running",
        summary={},
        owner_id=principal.user_id,
        org_id=principal.org_id,
    )
    db.add(s)
    await db.flush()
    await audit(
        db,
        principal,
        "agent.session",
        "agent_session",
        str(s.id),
        {"message": body.message[:500]},
        request,
    )
    await db.commit()
    await db.refresh(s)
    runner.launch(
        s.id,
        runner.start_input(body.message, body.region_id, str(body.run_id) if body.run_id else None),
    )
    return _out(s)


@router.get("/sessions", response_model=list[SessionOut])
async def list_sessions(
    db: AsyncSession = Depends(get_session), principal: Principal = Depends(get_principal)
) -> list[SessionOut]:
    rows = (
        await db.execute(
            select(AgentSession)
            .where(AgentSession.org_id == principal.org_id)
            .order_by(AgentSession.created_at.desc())
            .limit(50)
        )
    ).scalars()
    return [_out(s) for s in rows]


@router.get("/sessions/{session_id}", response_model=SessionOut)
async def get_agent_session(
    session_id: uuid.UUID, db: AsyncSession = Depends(get_session)
) -> SessionOut:
    return _out(await _get(db, session_id))


@router.post("/sessions/{session_id}/confirm", response_model=SessionOut, status_code=202)
async def confirm(
    session_id: uuid.UUID,
    body: ConfirmIn,
    request: Request,
    db: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> SessionOut:
    _require_llm()
    s = await _get(db, session_id)
    if s.status != "awaiting_confirmation":
        raise HTTPException(409, "This session isn't waiting for confirmation")
    s.status = "running"
    await audit(
        db,
        principal,
        "agent.confirm",
        "agent_session",
        str(session_id),
        {"approved": body.approved},
        request,
    )
    await db.commit()
    runner.launch(session_id, runner.resume_input(body.approved, body.note))
    return _out(s)


@router.post("/sessions/{session_id}/messages", response_model=SessionOut, status_code=202)
async def follow_up(
    session_id: uuid.UUID, body: MessageIn, db: AsyncSession = Depends(get_session)
) -> SessionOut:
    """Ask a follow-up in the same session (the run, corridor and facts carry over)."""
    _require_llm()
    s = await _get(db, session_id)
    if s.status not in ("completed", "stopped"):
        raise HTTPException(409, f"The session is {s.status}; wait for it to finish")
    s.status, s.request = "running", body.message
    await db.commit()
    await runner.emit(session_id, "user_message", None, {"message": body.message})
    runner.launch(session_id, runner.follow_up_input(body.message))
    return _out(s)


@router.post("/sessions/{session_id}/resume", response_model=SessionOut, status_code=202)
async def resume(session_id: uuid.UUID, db: AsyncSession = Depends(get_session)) -> SessionOut:
    """Continue an interrupted session from its last checkpoint."""
    _require_llm()
    s = await _get(db, session_id)
    if s.status != "interrupted":
        raise HTTPException(409, "Only interrupted sessions can be resumed")
    s.status = "running"
    await db.commit()
    runner.launch(session_id, None)
    return _out(s)


@router.post("/sessions/{session_id}/cancel", response_model=SessionOut)
async def cancel(session_id: uuid.UUID, db: AsyncSession = Depends(get_session)) -> SessionOut:
    s = await _get(db, session_id)
    if not runner.cancel(session_id):
        raise HTTPException(409, "The session isn't running")
    return _out(s)


@router.get("/sessions/{session_id}/calls")
async def llm_calls(
    session_id: uuid.UUID, db: AsyncSession = Depends(get_session)
) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            select(AgentLLMCall)
            .where(AgentLLMCall.session_id == session_id)
            .order_by(AgentLLMCall.id)
        )
    ).scalars()
    return [
        {
            "agent": c.agent,
            "model": c.model,
            "prompt": f"{c.prompt_name}@{c.prompt_version}",
            "input_tokens": c.input_tokens,
            "output_tokens": c.output_tokens,
            "cache_read_tokens": c.cache_read_tokens,
            "cache_write_tokens": c.cache_write_tokens,
            "cost_usd": c.cost_usd,
            "latency_ms": c.latency_ms,
            "stop_reason": c.stop_reason,
            "created_at": c.created_at,
        }
        for c in rows
    ]


def _sse(event_id: int, event: str, data: dict[str, Any]) -> str:
    return f"id: {event_id}\nevent: {event}\ndata: {json.dumps(data, default=str)}\n\n"


@router.get("/sessions/{session_id}/events")
async def events(
    session_id: uuid.UUID,
    after: int = Query(0, ge=0, description="Last event id already received"),
) -> StreamingResponse:
    """Server-sent events (Section 10.4 types) from `after` until the session finishes or
    pauses for confirmation."""

    async def stream() -> AsyncIterator[str]:
        last = after
        while True:
            async with async_session_factory() as db:
                s = await db.get(AgentSession, session_id)
                if s is None:
                    yield _sse(0, "error", {"message": "session not found"})
                    return
                rows = (
                    (
                        await db.execute(
                            select(AgentEvent)
                            .where(AgentEvent.session_id == session_id, AgentEvent.id > last)
                            .order_by(AgentEvent.id)
                            .limit(500)
                        )
                    )
                    .scalars()
                    .all()
                )
                status = s.status
                if status == "running" and not runner.is_running(session_id):
                    status = "interrupted"
            for e in rows:
                last = e.id
                yield _sse(e.id, e.type, {"agent": e.agent, "at": e.created_at, **e.data})
            if not rows and status != "running":
                yield _sse(last, "status", {"status": status})
                return
            await asyncio.sleep(0.4)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

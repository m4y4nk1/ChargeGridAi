"""Agent sessions, their streamed events and LLM-call traces (Section 10), and the policy
corpus for retrieval (policy.search)."""

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import BigInteger, Date, DateTime, Float, ForeignKey, Integer, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import UserDefinedType

from app.db.base import Base
from app.db.models.common import TimestampMixin

EMBEDDING_DIM = 384  # BAAI/bge-small-en-v1.5


class Vector(UserDefinedType):  # type: ignore[type-arg]
    """pgvector column; values are written and read as '[x,y,...]' literals in SQL."""

    cache_ok = True

    def __init__(self, dim: int) -> None:
        self.dim = dim

    def get_col_spec(self, **kw: Any) -> str:
        return f"vector({self.dim})"


class AgentSession(TimestampMixin, Base):
    __tablename__ = "agent_session"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # running | awaiting_confirmation | completed | failed | cancelled
    status: Mapped[str] = mapped_column(Text, default="running")
    request: Mapped[str] = mapped_column(Text)
    region_id: Mapped[str | None] = mapped_column(Text)
    scenario_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    # Latest plan, confirmation payload and validated report (the full state lives in
    # the LangGraph checkpointer, keyed by this session id).
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    tool_calls: Mapped[int] = mapped_column(Integer, default=0)
    llm_calls: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    org_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)


class AgentEvent(Base):
    """One SSE event (Section 10.4 types), in order per session."""

    __tablename__ = "agent_event"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_session.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[str] = mapped_column(Text)
    agent: Mapped[str | None] = mapped_column(Text)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AgentLLMCall(Base):
    """Trace of every model call: prompt version, model, tokens, cost (local stand-in for
    Langfuse, Section 10.3)."""

    __tablename__ = "agent_llm_call"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_session.id", ondelete="CASCADE"), index=True
    )
    agent: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    prompt_name: Mapped[str] = mapped_column(Text)
    prompt_version: Mapped[str] = mapped_column(Text)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    stop_reason: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PolicyDocument(TimestampMixin, Base):
    __tablename__ = "policy_document"

    id: Mapped[str] = mapped_column(Text, primary_key=True)  # e.g. mop_evci_2024
    title: Mapped[str] = mapped_column(Text)
    issuer: Mapped[str] = mapped_column(Text)
    issued_on: Mapped[date | None] = mapped_column(Date)
    url: Mapped[str | None] = mapped_column(Text)
    pages: Mapped[int] = mapped_column(Integer)
    snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("dataset_snapshot.id", ondelete="SET NULL")
    )


class PolicyChunk(Base):
    __tablename__ = "policy_chunk"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("policy_document.id", ondelete="CASCADE"), index=True
    )
    page: Mapped[int] = mapped_column(Integer)
    chunk_index: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    # Full-text (GIN on to_tsvector) and vector (HNSW) indexes are created in the
    # migration; SQLAlchemy can't express them portably.
    embedding: Mapped[Any] = mapped_column(Vector(EMBEDDING_DIM))

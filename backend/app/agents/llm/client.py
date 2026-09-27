"""Model access for the agents: one place that calls the Anthropic API, prices every
call, logs it (agent_llm_call) and enforces the session's limits.

The planner runs on Claude Opus 5.5 and specialists/report on Claude Sonnet 5 by default
(config/agents.yaml, overridable from .env). Notes that shape the calls:

- Opus 5.5 always thinks (adaptive); `output_config.effort` is the depth control and is
  set explicitly (its default is medium). Forced `tool_choice` is rejected, so tools
  are offered with `auto` and the prompt says which one to call.
- History is append-only: assistant turns are sent back exactly as returned (including
  thinking blocks), never edited.
- A `refusal` stop reason is surfaced as an error, not retried.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import anthropic

from app.agents.prompts import Prompt
from app.core.assumptions import load_config
from app.core.settings import get_settings


class AgentLimitExceeded(RuntimeError):
    pass


class LLMNotConfigured(RuntimeError):
    pass


class LLMRefused(RuntimeError):
    pass


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass
class LLMReply:
    """What the agents need from a model turn, independent of the SDK objects."""

    content: list[dict[str, Any]]  # assistant content blocks, to append verbatim
    stop_reason: str | None
    usage: Usage
    model: str
    request_id: str | None = None

    @property
    def text(self) -> str:
        return "".join(b.get("text", "") for b in self.content if b.get("type") == "text")

    @property
    def tool_uses(self) -> list[dict[str, Any]]:
        return [b for b in self.content if b.get("type") == "tool_use"]


class ModelBackend(Protocol):
    async def create(
        self,
        *,
        model: str,
        system: str,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
        max_tokens: int,
        effort: str,
        on_text: Callable[[str], Awaitable[None]] | None = None,
    ) -> LLMReply: ...


def _blocks(content: Sequence[Any]) -> list[dict[str, Any]]:
    """SDK content blocks -> plain dicts that can be checkpointed and sent back unchanged."""
    return [b.to_dict() if hasattr(b, "to_dict") else dict(b) for b in content]


class AnthropicBackend:
    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise LLMNotConfigured(
                "ANTHROPIC_API_KEY is not set; add it to .env to enable the agents"
            )
        self._client = anthropic.AsyncAnthropic(api_key=api_key)

    async def create(
        self,
        *,
        model: str,
        system: str,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
        max_tokens: int,
        effort: str,
        on_text: Callable[[str], Awaitable[None]] | None = None,
    ) -> LLMReply:
        params: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            # Stable system prompt + tool list first, so the prefix caches across turns.
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": list(messages),
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": effort},
        }
        if tools:
            params["tools"] = list(tools)
        # Streaming keeps long turns clear of HTTP timeouts and feeds partial_text.
        async with self._client.messages.stream(**params) as stream:
            if on_text is not None:
                async for text in stream.text_stream:
                    await on_text(text)
            message = await stream.get_final_message()
        u = message.usage
        return LLMReply(
            content=_blocks(message.content),
            stop_reason=message.stop_reason,
            usage=Usage(
                input_tokens=u.input_tokens or 0,
                output_tokens=u.output_tokens or 0,
                cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
                cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
            ),
            model=message.model,
            request_id=getattr(message, "_request_id", None),
        )


def agents_config() -> dict[str, Any]:
    return load_config("agents.yaml")


def model_for(role: str) -> str:
    settings = get_settings()
    cfg = agents_config()["models"]
    if role == "planner" and settings.llm_model_planner:
        return settings.llm_model_planner
    if role in ("report", "specialist") and settings.llm_model_report:
        return settings.llm_model_report
    return str(cfg[role])


def price_usd(model: str, usage: Usage) -> float:
    table = agents_config()["pricing_usd_per_mtok"]
    p = table.get(model) or next((v for k, v in table.items() if model.startswith(k)), None)
    if p is None:
        return 0.0
    return (
        float(
            usage.input_tokens * p["input"]
            + usage.output_tokens * p["output"]
            + usage.cache_read_tokens * p["cache_read"]
            + usage.cache_write_tokens * p["cache_write"]
        )
        / 1_000_000
    )


@dataclass
class Budget:
    """Per-session counters checked before every call (Section 10.3 limits)."""

    max_usd: float
    max_llm_calls: int
    max_tool_calls: int
    usd: float = 0.0
    llm_calls: int = 0
    tool_calls: int = 0
    tool_calls_by_agent: dict[str, int] = field(default_factory=dict)

    def check_llm(self) -> None:
        if self.llm_calls >= self.max_llm_calls:
            raise AgentLimitExceeded(f"reached the limit of {self.max_llm_calls} model calls")
        if self.usd >= self.max_usd:
            raise AgentLimitExceeded(f"reached the ${self.max_usd:.2f} spend limit")

    def check_tool(self, agent: str, per_agent: int) -> None:
        if self.tool_calls >= self.max_tool_calls:
            raise AgentLimitExceeded(f"reached the limit of {self.max_tool_calls} tool calls")
        if self.tool_calls_by_agent.get(agent, 0) >= per_agent:
            raise AgentLimitExceeded(f"{agent} reached its limit of {per_agent} tool calls")


TraceSink = Callable[[dict[str, Any]], Awaitable[None]]


class LLM:
    """Calls a backend for an agent, prices and traces the call, and enforces the budget."""

    def __init__(self, backend: ModelBackend, budget: Budget, trace: TraceSink) -> None:
        self.backend = backend
        self.budget = budget
        self._trace = trace

    async def turn(
        self,
        *,
        agent: str,
        role: str,
        prompt: Prompt,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
        on_text: Callable[[str], Awaitable[None]] | None = None,
    ) -> LLMReply:
        self.budget.check_llm()
        cfg = agents_config()
        model = model_for(role)
        started = time.perf_counter()
        reply = await self.backend.create(
            model=model,
            system=prompt.text,
            messages=messages,
            tools=tools,
            max_tokens=int(cfg["max_tokens"][role]),
            effort=str(cfg["effort"][role]),
            on_text=on_text,
        )
        cost = price_usd(model, reply.usage)
        self.budget.llm_calls += 1
        self.budget.usd += cost
        await self._trace(
            {
                "agent": agent,
                "model": reply.model or model,
                "prompt_name": prompt.name,
                "prompt_version": prompt.version,
                "input_tokens": reply.usage.input_tokens,
                "output_tokens": reply.usage.output_tokens,
                "cache_read_tokens": reply.usage.cache_read_tokens,
                "cache_write_tokens": reply.usage.cache_write_tokens,
                "cost_usd": round(cost, 6),
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "stop_reason": reply.stop_reason,
                "request_id": reply.request_id,
            }
        )
        if reply.stop_reason == "refusal":
            raise LLMRefused(f"the model declined this request ({agent})")
        return reply


class ScriptedBackend:
    """Offline backend for tests and evals without a key: replays prepared replies.

    `script(agent_hint, messages, tools)` returns the next LLMReply; the agent name is
    read from the system prompt's first line.
    """

    def __init__(self, script: Callable[[str, Sequence[dict[str, Any]]], LLMReply]) -> None:
        self._script = script
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kw: Any) -> LLMReply:
        self.calls.append(kw)
        agent = kw["system"].splitlines()[0].strip("# ").split(" ")[0].lower()
        reply = self._script(agent, kw["messages"])
        on_text = kw.get("on_text")
        if on_text is not None and reply.text:
            await on_text(reply.text)
        return reply

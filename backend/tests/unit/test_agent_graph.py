"""Agent layer offline: tool contracts, the tool-use loop, and graph routing with the
confirmation interrupt. No model, database or network."""

import re
import uuid
from typing import Any

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from pydantic import BaseModel

from app.agents import graph as graph_mod
from app.agents import runtime
from app.agents.llm.client import LLM, Budget, LLMReply, ScriptedBackend, Usage
from app.agents.prompts import load_prompt
from app.agents.runtime import AGENTS, AgentResult, run_agent
from app.agents.tools import ALL_TOOLS, BY_API_NAME, Tool, ToolContext, tools_for


def test_tool_contracts() -> None:
    names = [t.api_name for t in ALL_TOOLS]
    assert len(names) == len(set(names))
    for t in ALL_TOOLS:
        assert re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", t.api_name)
        spec = t.to_anthropic()
        assert spec["input_schema"]["type"] == "object"
        assert len(spec["description"]) > 40
    # Section 10.2 tools are all registered.
    for dotted in (
        "catalog.list_sources",
        "catalog.freshness",
        "gov.search_dataset",
        "geo.corridor",
        "geo.isochrone",
        "geo.query_features",
        "places.search_nearby",
        "routing.matrix",
        "chargers.search",
        "demand.forecast",
        "gap.compute",
        "candidates.generate",
        "feasibility.run",
        "score.run",
        "optimizer.optimize_network",
        "whatif.apply",
        "sizing.size_site",
        "energy.optimise",
        "finance.calculate",
        "results.get_run",
        "results.get_site",
        "results.why_not",
        "evidence.get",
        "policy.search",
    ):
        assert dotted.replace(".", "_") in BY_API_NAME, dotted


def test_report_reads_stored_results_only() -> None:
    groups = {t.group for t in tools_for(AGENTS["report"].grants)}
    assert groups == {"results", "evidence", "policy"}
    for name, spec in AGENTS.items():
        if name != "planner":
            assert "optimizer" not in {t.group for t in tools_for(spec.grants)}


def test_prompts_load_with_versions_and_shared_rules() -> None:
    for name in AGENTS:
        p = load_prompt(name)
        assert p.version and p.text.splitlines()[0].startswith(f"# {name} ")
        assert "Numbers come from tools" in p.text


class EchoIn(BaseModel):
    value: int


async def _echo(ctx: ToolContext, args: EchoIn) -> dict[str, Any]:
    return {"value": args.value * 2, "evidence_ids": ["ev-1"], "summary": "doubled"}


def _reply(*blocks: dict[str, Any]) -> LLMReply:
    return LLMReply(list(blocks), "tool_use", Usage(10, 5), "scripted")


def _ctx(events: list[Any]) -> ToolContext:
    async def emit(t: str, a: str | None, d: dict[str, Any]) -> None:
        events.append((t, a, d))

    return ToolContext(
        uuid.uuid4(),
        {},
        httpx.AsyncClient(),
        None,
        emit,
        Budget(1.0, 10, 5),  # type: ignore[arg-type]
    )


async def test_run_agent_executes_tools_and_returns_findings(monkeypatch: Any) -> None:
    echo = Tool(
        "catalog.echo", "catalog", "Echo a doubled value for testing purposes only.", EchoIn, _echo
    )
    monkeypatch.setitem(BY_API_NAME, echo.api_name, echo)
    monkeypatch.setattr(runtime, "tools_for", lambda grants: [echo])

    def script(agent: str, messages: Any) -> LLMReply:
        turn = sum(1 for m in messages if m["role"] == "assistant")
        if turn == 0:
            return _reply(
                {"type": "tool_use", "id": "t1", "name": "catalog_echo", "input": {"value": 21}},
                {"type": "tool_use", "id": "t2", "name": "geo_corridor", "input": {}},
            )
        return _reply(
            {
                "type": "tool_use",
                "id": "t3",
                "name": "submit_findings",
                "input": {"summary": "value is 42"},
            }
        )

    traces: list[dict[str, Any]] = []

    async def sink(call: dict[str, Any]) -> None:
        traces.append(call)

    events: list[Any] = []
    ctx = _ctx(events)
    llm = LLM(ScriptedBackend(script), ctx.budget, sink)
    result = await run_agent("data_discovery", llm, ctx, [{"role": "user", "content": "go"}])
    assert result.output["summary"] == "value is 42"
    assert 42.0 in ctx.facts and ctx.evidence_ids == ["ev-1"]
    tool_results = result.messages[2]["content"]
    assert tool_results[0]["content"].startswith('{"value": 42')
    assert tool_results[1]["is_error"] and "not available" in tool_results[1]["content"]
    assert [e[0] for e in events] == ["tool_call", "evidence", "tool_result_summary"]
    assert len(traces) == 2 and traces[0]["prompt_name"] == "data_discovery"
    assert ctx.budget.tool_calls == 1


async def test_graph_routes_pauses_for_confirmation_and_validates_report(
    monkeypatch: Any,
) -> None:
    calls: list[str] = []
    plan = {
        "intent": "plan",
        "restated_request": "r",
        "scenario": {"name": "s", "target_year": 2030, "charger_classes": ["DC_60"]},
        "steps": [],
        "consult": [],
        "assumptions": [],
        "questions_for_user": ["Buses?"],
    }

    async def fake_run_agent(
        name: str, llm: Any, ctx: ToolContext, messages: Any, **kw: Any
    ) -> AgentResult:
        calls.append(name)
        if name == "planner":
            return AgentResult(plan, messages)
        if name == "report":
            regen = isinstance(messages[-1]["content"], str) and messages[-1]["content"].startswith(
                "These numbers"
            )
            text = "Plan: 20 sites for ₹28.78 crore." + ("" if regen else " Payback 3 years.")
            return AgentResult({"text": text}, [*messages, {"role": "assistant", "content": text}])
        ctx.facts.extend([20.0, 287_760_000.0])
        return AgentResult({"summary": f"{name} done"}, messages)

    async def fake_system_tool(
        ctx: ToolContext, deps: Any, tool: str, args: dict[str, Any]
    ) -> dict[str, Any]:
        calls.append(tool)
        ctx.state["run_id"] = "run-1"
        return {"ok": True}

    monkeypatch.setattr(graph_mod, "run_agent", fake_run_agent)
    monkeypatch.setattr(graph_mod, "_system_tool", fake_system_tool)
    events: list[Any] = []

    async def emit(t: str, a: str | None, d: dict[str, Any]) -> None:
        events.append(t)

    llm = LLM(ScriptedBackend(lambda a, m: _reply()), Budget(1.0, 10, 10), emit)  # type: ignore[arg-type]
    deps = graph_mod.Deps(uuid.uuid4(), llm, httpx.AsyncClient(), None, emit)  # type: ignore[arg-type]
    g = graph_mod.build_graph().compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "t"}}
    out = await g.ainvoke(
        {"request": "Find 20 sites for ₹30 crore", "findings": []}, config, context=deps
    )
    assert "__interrupt__" in out
    payload = out["__interrupt__"][0].value
    assert payload["questions_for_user"] == ["Buses?"] and payload["run_id"] == "run-1"
    assert calls == [
        "planner",
        "data_discovery",
        "scenario.create",
        "candidates.generate",
        "geo",
        "demand",
    ]
    out = await g.ainvoke(Command(resume={"approved": True}), config, context=deps)
    assert calls[6:] == [
        "optimizer.optimize_network",
        "infrastructure",
        "grid_energy",
        "finance",
        "report",
        "report",
    ]
    report = out["report"]
    assert report["markdown"] == "Plan: 20 sites for ₹28.78 crore."
    assert report["validation"]["regenerations"] == 1
    assert report["validation"]["unmatched_after"] == 0
    assert "plan" in events


async def test_declined_confirmation_goes_to_report(monkeypatch: Any) -> None:
    calls: list[str] = []

    async def fake_run_agent(
        name: str, llm: Any, ctx: ToolContext, messages: Any, **kw: Any
    ) -> AgentResult:
        calls.append(name)
        if name == "planner":
            return AgentResult(
                {
                    "intent": "plan",
                    "restated_request": "r",
                    "scenario": {"name": "s", "target_year": 2030, "charger_classes": ["DC_60"]},
                },
                messages,
            )
        return AgentResult(
            {"text": "Not optimised."} if name == "report" else {"summary": "ok"}, messages
        )

    async def fake_system_tool(*a: Any) -> dict[str, Any]:
        return {"ok": True}

    monkeypatch.setattr(graph_mod, "run_agent", fake_run_agent)
    monkeypatch.setattr(graph_mod, "_system_tool", fake_system_tool)

    async def emit(*a: Any) -> None:
        return None

    llm = LLM(ScriptedBackend(lambda a, m: _reply()), Budget(1.0, 10, 10), emit)  # type: ignore[arg-type]
    deps = graph_mod.Deps(uuid.uuid4(), llm, httpx.AsyncClient(), None, emit)  # type: ignore[arg-type]
    g = graph_mod.build_graph().compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "t2"}}
    await g.ainvoke({"request": "plan it", "findings": []}, config, context=deps)
    out = await g.ainvoke(Command(resume={"approved": False}), config, context=deps)
    assert calls[-1] == "report" and "finance" not in calls
    assert out["report"]["markdown"] == "Not optimised."


@pytest.mark.parametrize("consult", [["demand"], []])
async def test_question_consults_named_specialists(monkeypatch: Any, consult: list[str]) -> None:
    calls: list[str] = []

    async def fake_run_agent(
        name: str, llm: Any, ctx: ToolContext, messages: Any, **kw: Any
    ) -> AgentResult:
        calls.append(name)
        if name == "planner":
            return AgentResult(
                {"intent": "question", "restated_request": "q", "consult": consult}, messages
            )
        return AgentResult({"text": "Answer."} if name == "report" else {"summary": "ok"}, messages)

    monkeypatch.setattr(graph_mod, "run_agent", fake_run_agent)

    async def emit(*a: Any) -> None:
        return None

    llm = LLM(ScriptedBackend(lambda a, m: _reply()), Budget(1.0, 10, 10), emit)  # type: ignore[arg-type]
    deps = graph_mod.Deps(uuid.uuid4(), llm, httpx.AsyncClient(), None, emit)  # type: ignore[arg-type]
    g = graph_mod.build_graph().compile(checkpointer=InMemorySaver())
    out = await g.ainvoke(
        {"request": "how many chargers?", "findings": []},
        {"configurable": {"thread_id": "q"}},
        context=deps,
    )
    assert calls == ["planner", *consult, "report"]
    assert out["report"]["markdown"] == "Answer."

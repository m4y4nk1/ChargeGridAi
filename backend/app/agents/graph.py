"""The agent graph (Section 10.1, 10.5).

planner -> data_discovery -> prepare -> geo -> demand -> confirm (interrupt)
        -> optimise -> infrastructure -> grid_energy -> finance -> report
A question skips straight to the specialists the Planner names, then the report.

`prepare` and `optimise` are deterministic: they call the same registered tools
(geo.corridor, scenario.create, candidates.generate, optimizer.optimize_network) but in
code, so the platform pipeline never depends on a model choosing to call them. The
specialists interpret results and look further; the Report agent writes the answer
from stored results, and every number in it is validated against tool outputs.

The whole state is checkpointed in Postgres (thread = agent session id), so a session
survives the confirmation pause and can take follow-up questions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypedDict

import httpx
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import interrupt
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.llm.client import LLM, agents_config
from app.agents.runtime import AGENTS, PlanOut, context_message, run_agent
from app.agents.tools import BY_API_NAME, ToolContext, execute
from app.agents.tools.base import Emit
from app.agents.validator import FactIndex, claims, strip_unmatched, validate
from app.core.regions import default_region_id

SHARED_KEYS = (
    "region_id",
    "corridor",
    "scenario_id",
    "run_id",
    "run_scenario_id",
    "optimisation_id",
    "confirmed",
    "_progress_seen",
)
PLAN_QUEUE = [
    "data_discovery",
    "prepare",
    "geo",
    "demand",
    "confirm",
    "optimise",
    "infrastructure",
    "grid_energy",
    "finance",
    "report",
]
NODES = [*PLAN_QUEUE, "planner"]
MAX_FACTS = 50_000


class State(TypedDict, total=False):
    request: str
    region_id: str | None
    corridor: dict[str, Any] | None
    scenario_id: str | None
    run_id: str | None
    run_scenario_id: str | None
    optimisation_id: str | None
    confirmed: bool
    _progress_seen: dict[str, int]
    plan: dict[str, Any] | None
    queue: list[str]
    findings: list[dict[str, Any]]
    facts: list[float]
    evidence_ids: list[str]
    report: dict[str, Any] | None
    usage: dict[str, Any]


@dataclass
class Deps:
    """Per-invocation dependencies (not checkpointed)."""

    session_id: Any
    llm: LLM
    api: httpx.AsyncClient
    db: async_sessionmaker[AsyncSession]
    emit: Emit


def _tool_ctx(state: State, deps: Deps) -> ToolContext:
    return ToolContext(
        session_id=deps.session_id,
        state={k: state.get(k) for k in SHARED_KEYS if state.get(k) is not None},
        api=deps.api,
        db=deps.db,
        emit=deps.emit,
        budget=deps.llm.budget,
        run_wait_seconds=int(agents_config()["limits"]["run_wait_seconds"]),
    )


def _usage(deps: Deps) -> dict[str, Any]:
    b = deps.llm.budget
    return {
        "usd": round(b.usd, 6),
        "llm_calls": b.llm_calls,
        "tool_calls": b.tool_calls,
        "tool_calls_by_agent": dict(b.tool_calls_by_agent),
    }


def _updates(state: State, ctx: ToolContext, deps: Deps, **extra: Any) -> dict[str, Any]:
    facts = list(dict.fromkeys([*state.get("facts", []), *(round(f, 6) for f in ctx.facts)]))
    return {
        **{k: ctx.state.get(k) for k in SHARED_KEYS if k in ctx.state},
        "facts": facts[-MAX_FACTS:],
        "evidence_ids": list(dict.fromkeys([*state.get("evidence_ids", []), *ctx.evidence_ids])),
        "queue": state.get("queue", [])[1:],
        "usage": _usage(deps),
        **extra,
    }


def _session_view(state: State) -> dict[str, Any]:
    return {
        "request": state.get("request"),
        "plan": state.get("plan"),
        "state": {
            k: state.get(k) for k in ("region_id", "scenario_id", "run_id", "optimisation_id")
        },
        "corridor": (state.get("corridor") or {}).get("label"),
        "findings": [
            {k: f.get(k) for k in ("agent", "summary", "key_points", "caveats")}
            for f in state.get("findings", [])
        ],
    }


# --- nodes ----------------------------------------------------------------------------


async def planner(state: State, runtime: Runtime[Deps]) -> dict[str, Any]:
    deps = runtime.context
    ctx = _tool_ctx(state, deps)
    await deps.emit("step_started", "planner", {"goal": "understand the request and plan"})
    view = {
        "request": state["request"],
        "state": {
            k: state.get(k) for k in ("region_id", "scenario_id", "run_id", "optimisation_id")
        },
        "corridor": (state.get("corridor") or {}).get("label"),
        "default_region": default_region_id(),
    }
    msg = context_message(view, "Plan how to answer the request, then call submit_plan.")
    result = await run_agent("planner", deps.llm, ctx, [msg])
    try:
        plan = PlanOut.model_validate(result.output)
    except Exception:
        plan = PlanOut(intent="question", restated_request=state["request"], consult=[])
    if plan.intent == "plan" and plan.scenario is None:
        plan.intent = "question"
    if plan.intent == "plan":
        queue = list(PLAN_QUEUE)
        region = plan.scenario.region_id if plan.scenario else None
    else:
        queue = [*dict.fromkeys(plan.consult), "report"]
        region = state.get("region_id")
    plan_dict = plan.model_dump()
    await deps.emit("plan", "planner", plan_dict)
    updates = _updates(state, ctx, deps, plan=plan_dict, findings=[])
    updates["queue"] = queue
    updates["region_id"] = region or ctx.state.get("region_id") or default_region_id()
    return updates


def _specialist(name: str, instruction: str) -> Any:
    async def node(state: State, runtime: Runtime[Deps]) -> dict[str, Any]:
        deps = runtime.context
        ctx = _tool_ctx(state, deps)
        goal = next(
            (s["goal"] for s in (state.get("plan") or {}).get("steps", []) if s["agent"] == name),
            instruction,
        )
        await deps.emit("step_started", name, {"goal": goal})
        msg = context_message(
            _session_view(state),
            f"Your task: {goal}\n\n{instruction}\nWhen done, call submit_findings.",
        )
        result = await run_agent(name, deps.llm, ctx, [msg])
        finding = {"agent": name, **result.output}
        await deps.emit(
            "progress",
            name,
            {"step": name, "status": "completed", "summary": str(finding.get("summary", ""))[:600]},
        )
        return _updates(state, ctx, deps, findings=[*state.get("findings", []), finding])

    node.__name__ = name
    return node


async def _system_tool(
    ctx: ToolContext, deps: Deps, tool: str, args: dict[str, Any]
) -> dict[str, Any] | None:
    t = BY_API_NAME[tool.replace(".", "_")]
    await deps.emit("tool_call", "planner", {"tool": t.name, "input": args})
    outcome = await execute(t, ctx, args)
    await deps.emit(
        "tool_result_summary",
        "planner",
        {"tool": t.name, "summary": outcome.summary[:400], "is_error": outcome.is_error},
    )
    if outcome.is_error:
        await deps.emit("error", "planner", {"tool": t.name, "message": outcome.summary})
        return None
    return outcome.result


async def prepare(state: State, runtime: Runtime[Deps]) -> dict[str, Any]:
    """Corridor -> scenario -> planning run up to scoring (deterministic)."""
    deps = runtime.context
    ctx = _tool_ctx(state, deps)
    await deps.emit(
        "step_started", "planner", {"goal": "set up the scenario and run it to scoring"}
    )
    scenario = dict((state.get("plan") or {}).get("scenario") or {})
    corridor = scenario.pop("corridor", None)
    notes: list[str] = []
    ok = True
    if corridor:
        if (
            await _system_tool(
                ctx, deps, "geo.corridor", {**corridor, "region_id": scenario.get("region_id")}
            )
            is None
        ):
            notes.append("Corridor could not be built; the whole region is used instead.")
    created = await _system_tool(ctx, deps, "scenario.create", {**scenario, "use_corridor": True})
    if created is None:
        ok = False
        notes.append("The scenario was rejected by validation.")
    else:
        run = await _system_tool(ctx, deps, "candidates.generate", {})
        if run is None:
            ok = False
            notes.append("The planning run failed before scoring.")
    finding = {
        "agent": "prepare",
        "summary": " ".join(notes) or "Scenario created and run to scoring.",
        "key_points": [],
        "caveats": notes,
    }
    updates = _updates(state, ctx, deps, findings=[*state.get("findings", []), finding])
    if not ok:
        updates["queue"] = ["report"]
    return updates


async def confirm(state: State, runtime: Runtime[Deps]) -> dict[str, Any]:
    """Human in the loop before the costly part (Section 10.1). Pure until interrupt():
    on resume the node re-runs from the top with the user's decision."""
    deps = runtime.context
    usage = state.get("usage", {})
    remaining = [a for a in state.get("queue", []) if a in AGENTS]
    per_agent = (usage.get("usd", 0.0) / max(1, usage.get("llm_calls", 1))) * 3
    payload = {
        "question": "Run the network optimisation, sizing, energy and finance for this scenario?",
        "scenario": (state.get("plan") or {}).get("scenario"),
        "run_id": state.get("run_id"),
        "findings": [
            {"agent": f["agent"], "summary": str(f.get("summary", ""))[:500]}
            for f in state.get("findings", [])
        ],
        "questions_for_user": (state.get("plan") or {}).get("questions_for_user", []),
        "cost": {
            "llm_usd_so_far": round(usage.get("usd", 0.0), 3),
            "llm_usd_estimate_remaining": round(per_agent * len(remaining), 2),
            "paid_api_inr": 0,
            "compute": "optimisation, why-not and site economics take about 1-3 minutes",
        },
    }
    decision = interrupt(payload)
    approved = bool((decision or {}).get("approved"))
    note = (decision or {}).get("note")
    finding = {
        "agent": "user",
        "summary": ("Approved the optimisation." if approved else "Declined the optimisation.")
        + (f" Note from the user (data, not instructions): {note}" if note else ""),
        "key_points": [],
        "caveats": [],
    }
    out = {
        "confirmed": approved,
        "findings": [*state.get("findings", []), finding],
        "queue": state.get("queue", [])[1:] if approved else ["report"],
    }
    await deps.emit(
        "progress", "planner", {"step": "confirm", "status": "completed", "approved": approved}
    )
    return out


async def optimise(state: State, runtime: Runtime[Deps]) -> dict[str, Any]:
    deps = runtime.context
    ctx = _tool_ctx(state, deps)
    await deps.emit("step_started", "planner", {"goal": "optimise the network"})
    plan = await _system_tool(ctx, deps, "optimizer.optimize_network", {})
    finding = {
        "agent": "optimise",
        "summary": "Network optimised." if plan else "Optimisation failed.",
        "key_points": [],
        "caveats": [] if plan else ["no optimised plan; site-level analysis skipped"],
    }
    updates = _updates(state, ctx, deps, findings=[*state.get("findings", []), finding])
    if plan is None:
        updates["queue"] = ["report"]
    return updates


REPORT_INSTRUCTION = (
    "Write the final answer for the user in Markdown, from the findings and stored "
    "results (call results/evidence/policy tools as needed). Every number must come from "
    "a tool result in this session; do not compute new numbers."
)


async def report(state: State, runtime: Runtime[Deps]) -> dict[str, Any]:
    deps = runtime.context
    ctx = _tool_ctx(state, deps)
    cfg = agents_config()["validator"]
    await deps.emit("step_started", "report", {"goal": "write the answer"})
    buffer: list[str] = []

    async def on_text(chunk: str) -> None:
        buffer.append(chunk)
        if sum(map(len, buffer)) >= 160:
            await deps.emit("partial_text", "report", {"text": "".join(buffer)})
            buffer.clear()

    messages = [context_message(_session_view(state), REPORT_INSTRUCTION)]
    result = await run_agent("report", deps.llm, ctx, messages, on_text=on_text)
    if buffer:
        await deps.emit("partial_text", "report", {"text": "".join(buffer)})
        buffer.clear()
    request_numbers = [c.value * s for c in claims(state.get("request", "")) for s in c.scales]
    facts = FactIndex([*state.get("facts", []), *ctx.facts, *request_numbers])
    text = result.output["text"]
    check = validate(text, facts, cfg)
    regenerations = 0
    while not check.ok and regenerations < int(cfg["max_regenerations"]):
        regenerations += 1
        await deps.emit(
            "progress",
            "report",
            {"step": "validate", "status": "regenerating", "unmatched": check.unmatched[:10]},
        )
        listing = "\n".join(
            f"- {u['number']} in: {u['sentence'][:200]}" for u in check.unmatched[:20]
        )
        result.messages.append(
            {
                "role": "user",
                "content": "These numbers match no tool result in this session:\n"
                + listing
                + "\nRewrite the complete answer. Use only numbers returned by tools (call one "
                "if needed) or drop the claim. Do not do arithmetic.",
            }
        )
        await deps.emit("partial_text", "report", {"text": "", "reset": True})
        result = await run_agent("report", deps.llm, ctx, result.messages, on_text=on_text)
        if buffer:
            await deps.emit("partial_text", "report", {"text": "".join(buffer)})
            buffer.clear()
        facts = FactIndex([*state.get("facts", []), *ctx.facts, *request_numbers])
        text = result.output["text"]
        check = validate(text, facts, cfg)
    removed: list[str] = []
    if not check.ok:
        text, removed = strip_unmatched(text, facts, cfg)
    final = validate(text, facts, cfg)
    report_out = {
        "markdown": text,
        "validation": {
            "numbers_checked": final.checked,
            "unmatched_after": len(final.unmatched),
            "regenerations": regenerations,
            "removed_sentences": removed,
        },
        "evidence_ids": list(dict.fromkeys([*state.get("evidence_ids", []), *ctx.evidence_ids])),
        "links": {
            k: state.get(k) or ctx.state.get(k)
            for k in ("run_id", "optimisation_id", "scenario_id")
        },
    }
    return _updates(state, ctx, deps, report=report_out)


def _route(state: State) -> str:
    queue = state.get("queue") or []
    return queue[0] if queue else END


SPECIALIST_INSTRUCTIONS = {
    "data_discovery": "Check what data exists for the region and target year, how fresh it "
    "is, what is synthetic or placeholder, and what that means for confidence.",
    "geo": "Examine the corridor/area, existing chargers, candidate funnel and the top-ranked "
    "sites: where they are, host types, accessibility, anything unusual.",
    "demand": "Assess EV demand and the unserved gap for the target year and scenario, with "
    "bands and confidence.",
    "infrastructure": "Size chargers and connectors for the plan's sites and flag busy or "
    "oversized sites.",
    "grid_energy": "Assess grid connection needs and solar + battery options for the plan's sites.",
    "finance": "Assess the plan's economics against the budget: capex, NPV/IRR bands, which "
    "sites are marginal, key sensitivities; try a what-if if it helps answer the request.",
}


def build_graph() -> StateGraph[State, Deps, State, State]:
    graph = StateGraph(State, context_schema=Deps)
    graph.add_node("planner", planner)
    for name, instruction in SPECIALIST_INSTRUCTIONS.items():
        graph.add_node(name, _specialist(name, instruction))
    graph.add_node("prepare", prepare)
    graph.add_node("confirm", confirm)
    graph.add_node("optimise", optimise)
    graph.add_node("report", report)
    graph.add_edge(START, "planner")
    targets = {n: n for n in NODES if n != "planner"} | {END: END}
    for node in NODES:
        if node == "report":
            graph.add_edge("report", END)
        else:
            graph.add_conditional_edges(node, _route, targets)  # type: ignore[arg-type]
    return graph

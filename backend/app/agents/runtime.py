"""Agent definitions (Section 10.1) and the tool-use loop each agent runs.

An agent is a system prompt, the tool groups it may call and, for all but the Report
agent, a control tool it calls to hand back structured output (submit_plan /
submit_findings). The loop appends the model's turns verbatim, runs the requested
tools, and stops when the control tool is called or the model ends its turn.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from app.agents.llm.client import LLM, AgentLimitExceeded, agents_config
from app.agents.prompts import load_prompt
from app.agents.tools import BY_API_NAME, ToolContext, execute, tools_for

Specialist = Literal["data_discovery", "geo", "demand", "infrastructure", "grid_energy", "finance"]


class CorridorSpec(BaseModel):
    from_place: str
    to_place: str
    via: list[str] = Field(default_factory=list, max_length=3)
    buffer_m: int = Field(default=5000, ge=500, le=50000)
    label: str | None = None


class ScenarioSpec(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    region_id: str | None = Field(default=None, description="pmr or mumbai_pune")
    target_year: int = Field(ge=2026, le=2035)
    adoption_case: Literal["slow", "base", "fast"] = "base"
    adoption_multiplier: float = Field(default=1.0, gt=0, le=5)
    charger_classes: list[str] = Field(min_length=1)
    budget_inr: int | None = Field(default=None, ge=0)
    max_sites: int | None = Field(default=None, ge=1, le=500)
    weight_profile: str | None = None
    corridor: CorridorSpec | None = None


class Step(BaseModel):
    agent: str
    goal: str = Field(max_length=300)


class PlanOut(BaseModel):
    """What the Planner hands to the graph."""

    intent: Literal["plan", "question"]
    restated_request: str = Field(max_length=600)
    scenario: ScenarioSpec | None = Field(
        default=None, description="Required when intent is 'plan'"
    )
    steps: list[Step] = Field(default_factory=list, max_length=12)
    consult: list[Specialist] = Field(
        default_factory=list,
        description="For intent 'question': specialists to consult before the report",
    )
    assumptions: list[str] = Field(default_factory=list, max_length=10)
    questions_for_user: list[str] = Field(default_factory=list, max_length=5)


class FindingsOut(BaseModel):
    summary: str = Field(max_length=2500, description="Markdown; every number from a tool")
    key_points: list[str] = Field(default_factory=list, max_length=10)
    caveats: list[str] = Field(default_factory=list, max_length=8)


@dataclass(frozen=True)
class Control:
    name: str
    description: str
    model: type[BaseModel]

    def to_anthropic(self) -> dict[str, Any]:
        schema = self.model.model_json_schema()
        schema.pop("title", None)
        return {"name": self.name, "description": self.description, "input_schema": schema}


SUBMIT_PLAN = Control(
    "submit_plan",
    "Hand the structured plan to the orchestrator. Call exactly once, when ready.",
    PlanOut,
)
SUBMIT_FINDINGS = Control(
    "submit_findings",
    "Hand your findings to the orchestrator. Call exactly once, as your last action.",
    FindingsOut,
)


@dataclass(frozen=True)
class AgentSpec:
    name: str
    title: str
    role: str  # model role in config/agents.yaml: planner | specialist | report
    grants: tuple[str, ...]
    control: Control | None


AGENTS: dict[str, AgentSpec] = {
    a.name: a
    for a in (
        AgentSpec(
            "planner",
            "Planner",
            "planner",
            ("catalog", "geo.find_place", "chargers", "results.get_run", "policy"),
            SUBMIT_PLAN,
        ),
        AgentSpec(
            "data_discovery",
            "Data Discovery",
            "specialist",
            ("catalog", "gov", "chargers"),
            SUBMIT_FINDINGS,
        ),
        AgentSpec(
            "geo",
            "Geo",
            "specialist",
            ("geo", "places", "routing", "chargers", "feasibility.run", "score.run"),
            SUBMIT_FINDINGS,
        ),
        AgentSpec(
            "demand", "Demand", "specialist", ("demand", "gap", "chargers", "twin"), SUBMIT_FINDINGS
        ),
        AgentSpec(
            "infrastructure",
            "Infrastructure",
            "specialist",
            ("sizing", "results.get_run"),
            SUBMIT_FINDINGS,
        ),
        AgentSpec(
            "grid_energy",
            "Grid & Energy",
            "specialist",
            ("grid", "energy", "results.get_run", "twin"),
            SUBMIT_FINDINGS,
        ),
        AgentSpec(
            "finance",
            "Finance",
            "specialist",
            ("finance", "whatif", "results.get_run"),
            SUBMIT_FINDINGS,
        ),
        AgentSpec("report", "Report", "report", ("results", "evidence", "policy"), None),
    )
}


OnText = Callable[[str], Awaitable[None]]


@dataclass
class AgentResult:
    output: dict[str, Any]  # control-tool input, or {"text": ...} for the Report agent
    messages: list[dict[str, Any]]


async def run_agent(
    name: str,
    llm: LLM,
    ctx: ToolContext,
    messages: list[dict[str, Any]],
    *,
    on_text: OnText | None = None,
) -> AgentResult:
    spec = AGENTS[name]
    prompt = load_prompt(name)
    tools = tools_for(spec.grants)
    allowed = {t.api_name for t in tools}
    tool_defs = [t.to_anthropic() for t in tools]
    if spec.control:
        tool_defs.append(spec.control.to_anthropic())
    limits = agents_config()["limits"]
    per_agent = int(limits["max_tool_calls_per_agent"])
    for _ in range(per_agent + 4):
        reply = await llm.turn(
            agent=name,
            role=spec.role,
            prompt=prompt,
            messages=messages,
            tools=tool_defs,
            on_text=on_text,
        )
        messages.append({"role": "assistant", "content": reply.content})
        if not reply.tool_uses:
            if spec.control is None:
                return AgentResult({"text": reply.text}, messages)
            # Ended without the control tool: take the text as the findings.
            return AgentResult(
                {
                    "summary": reply.text[:2500],
                    "key_points": [],
                    "caveats": ["agent did not submit structured findings"],
                },
                messages,
            )
        results: list[dict[str, Any]] = []
        submitted: dict[str, Any] | None = None
        for use in reply.tool_uses:
            if spec.control and use["name"] == spec.control.name:
                try:
                    submitted = spec.control.model.model_validate(use["input"]).model_dump()
                    content, is_error = "Recorded.", False
                except ValidationError as exc:
                    content = f"Invalid: {exc.errors(include_url=False)}"
                    is_error = True
                results.append(_result(use["id"], content, is_error))
                continue
            tool = BY_API_NAME.get(use["name"])
            if tool is None or use["name"] not in allowed:
                results.append(
                    _result(
                        use["id"],
                        f"Tool {use['name']} is not available to the {spec.title} agent.",
                        True,
                    )
                )
                continue
            try:
                llm.budget.check_tool(name, per_agent)
            except AgentLimitExceeded as exc:
                if llm.budget.tool_calls >= llm.budget.max_tool_calls:
                    raise
                results.append(_result(use["id"], f"{exc}. Submit what you have now.", True))
                continue
            llm.budget.tool_calls += 1
            llm.budget.tool_calls_by_agent[name] = llm.budget.tool_calls_by_agent.get(name, 0) + 1
            await ctx.emit("tool_call", name, {"tool": tool.name, "input": use["input"]})
            outcome = await execute(tool, ctx, use["input"])
            await ctx.emit(
                "tool_result_summary",
                name,
                {"tool": tool.name, "summary": outcome.summary[:400], "is_error": outcome.is_error},
            )
            results.append(_result(use["id"], outcome.content, outcome.is_error))
        messages.append({"role": "user", "content": results})
        if submitted is not None:
            return AgentResult(submitted, messages)
    return AgentResult(
        {
            "summary": "(stopped: turn limit reached)",
            "key_points": [],
            "caveats": ["turn limit reached"],
        },
        messages,
    )


def _result(tool_use_id: str, content: str, is_error: bool) -> dict[str, Any]:
    block: dict[str, Any] = {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}
    if is_error:
        block["is_error"] = True
    return block


def context_message(payload: dict[str, Any], instruction: str) -> dict[str, Any]:
    """The first user turn for an agent: session context as delimited data, then the task."""
    return {
        "role": "user",
        "content": (
            "<session_context>\n"
            + json.dumps(payload, ensure_ascii=False, default=str, indent=1)
            + "\n</session_context>\n\n"
            + instruction
        ),
    }

"""A scripted stand-in for the model, for offline tests and the no-key smoke run.

It plays each agent's part deterministically: the Planner submits a given plan, each
specialist calls one tool and submits its summary, and the Report agent reads the run
and writes a short answer from the numbers it was given. Optionally the first report
draft carries an unsupported number, to exercise the validator's regenerate path.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from app.agents.llm.client import LLMReply, ScriptedBackend, Usage

BOGUS = "₹987.65 crore"


def _reply(*blocks: dict[str, Any], stop: str = "end_turn") -> LLMReply:
    return LLMReply(
        content=list(blocks),
        stop_reason=stop,
        usage=Usage(input_tokens=1000, output_tokens=200),
        model="scripted",
    )


def tool_use(name: str, payload: dict[str, Any], n: int) -> dict[str, Any]:
    return {"type": "tool_use", "id": f"toolu_{name}_{n}", "name": name, "input": payload}


def text(t: str) -> dict[str, Any]:
    return {"type": "text", "text": t}


def _turns(messages: Sequence[dict[str, Any]]) -> int:
    return sum(1 for m in messages if m["role"] == "assistant")


def _last_result(messages: Sequence[dict[str, Any]]) -> dict[str, Any]:
    for m in reversed(messages):
        if m["role"] == "user" and isinstance(m["content"], list):
            for block in m["content"]:
                if block.get("type") == "tool_result":
                    raw = block["content"]
                    if raw.startswith("<retrieved_data>"):
                        raw = raw.split("\n", 2)[2].rsplit("\n", 1)[0]
                    try:
                        return dict(json.loads(raw))
                    except ValueError:
                        return {"error": raw}
    return {}


def crore(inr: float) -> str:
    return f"₹{inr / 1e7:.2f} crore"


DEFAULT_SPECIALIST_TOOLS: dict[str, tuple[str, dict[str, Any]]] = {
    "data_discovery": ("catalog_freshness", {}),
    "geo": ("chargers_search", {"area": {"corridor": True}, "min_kw": 25, "limit": 5}),
    "demand": ("gap_compute", {"year": 2030, "top_n": 3}),
    "infrastructure": ("sizing_size_site", {}),
    "grid_energy": ("grid_connection", {}),
    "finance": ("finance_calculate", {}),
}


class ScriptedFlow:
    def __init__(
        self,
        plan: dict[str, Any],
        specialist_tools: dict[str, tuple[str, dict[str, Any]]] | None = None,
        bad_first_draft: bool = True,
    ) -> None:
        self.plan = plan
        self.tools = {**DEFAULT_SPECIALIST_TOOLS, **(specialist_tools or {})}
        self.bad_first_draft = bad_first_draft

    def backend(self) -> ScriptedBackend:
        return ScriptedBackend(self)

    def __call__(self, agent: str, messages: Sequence[dict[str, Any]]) -> LLMReply:
        turn = _turns(messages)
        if agent == "planner":
            return _reply(
                text("Planning."), tool_use("submit_plan", self.plan, turn), stop="tool_use"
            )
        if agent == "report":
            return self._report(messages, turn)
        name, payload = self.tools[agent]
        if turn == 0:
            return _reply(tool_use(name, payload, turn), stop="tool_use")
        result = _last_result(messages)
        summary = str(result.get("summary") or result.get("error") or "no result")
        return _reply(
            tool_use(
                "submit_findings",
                {"summary": f"{agent}: {summary}", "key_points": [], "caveats": []},
                turn,
            ),
            stop="tool_use",
        )

    def _report(self, messages: Sequence[dict[str, Any]], turn: int) -> LLMReply:
        if turn == 0:
            return _reply(tool_use("results_get_run", {}, turn), stop="tool_use")
        last_user = messages[-1]["content"]
        regenerating = isinstance(last_user, str) and last_user.startswith("These numbers")
        run = next(
            (
                _parsed
                for m in messages
                if m["role"] == "user" and isinstance(m["content"], list)
                for b in m["content"]
                if b.get("type") == "tool_result" and not b.get("is_error")
                for _parsed in [json.loads(b["content"])]
                if "funnel" in _parsed
            ),
            None,
        )
        lines = ["## Answer", ""]
        if run is None:
            lines.append("No planning run is available for this request.")
        else:
            f = run["funnel"]
            lines.append(
                f"The run screened {f['candidates']} candidate sites; {f['feasible']} passed "
                f"feasibility and {f['scored']} were scored."
            )
            plan = run.get("plan") or {}
            if plan.get("sites"):
                cost = plan["kpis"].get("cost_inr")
                lines.append(
                    f"The plan selects {len(plan['sites'])} candidate sites"
                    + (f" costing {crore(cost)}." if cost else ".")
                )
                lines += ["", "| Site | Bundle | Charge points |", "|---|---|---|"]
                lines += [
                    f"| {s['name'] or 'Unnamed'} | {s['bundle']} | {s['charge_points']} |"
                    for s in plan["sites"][:10]
                ]
            elif plan.get("awaiting_confirmation"):
                lines.append("The optimisation was not run.")
        if self.bad_first_draft and not regenerating:
            lines += ["", f"Total investment would be {BOGUS}."]
        lines += ["", "Candidate sites require field verification."]
        return _reply(text("\n".join(lines)))


def corridor_plan() -> dict[str, Any]:
    """The Section 10.5 example: 20 fast-charging sites along Mumbai-Pune, ₹30 crore."""
    return {
        "intent": "plan",
        "restated_request": "Find 20 fast-charging locations along the Mumbai–Pune corridor "
        "with a ₹30 crore budget.",
        "scenario": {
            "name": "Mumbai–Pune fast charging, 20 sites, ₹30 Cr",
            "region_id": "mumbai_pune",
            "target_year": 2030,
            "adoption_case": "base",
            "adoption_multiplier": 1.0,
            "charger_classes": ["DC_60", "DC_120"],
            "budget_inr": 300_000_000,
            "max_sites": 20,
            "weight_profile": "highway",
            "corridor": {"from_place": "Mumbai", "to_place": "Pune", "buffer_m": 5000},
        },
        "steps": [
            {"agent": "data_discovery", "goal": "Check corridor data freshness and gaps"},
            {"agent": "geo", "goal": "Existing fast chargers along the corridor"},
            {"agent": "demand", "goal": "2030 demand and unserved gap in the corridor"},
            {"agent": "infrastructure", "goal": "Size chargers for the plan"},
            {"agent": "grid_energy", "goal": "Grid connection needs"},
            {"agent": "finance", "goal": "Budget fit and NPV bands"},
        ],
        "consult": [],
        "assumptions": ["Target year 2030", "DC 60/120 kW", "5 km corridor buffer"],
        "questions_for_user": ["Serve e-buses as well as e-4W?"],
    }

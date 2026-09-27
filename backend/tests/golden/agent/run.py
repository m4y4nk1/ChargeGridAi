"""Golden agent eval runner (Section 10.3).

    python tests/golden/agent/run.py [--cases q01_pune_dc_stations,r02_why_not]
                                     [--skip-plans] [--max-usd 15]

Runs each case in cases.yaml through the real agent graph (Claude API, real tools and
data), checks the expectations and prints a table. Exit 0 when >= 90% pass. Results
are written to tests/golden/agent/results/<timestamp>.json (gitignored).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import httpx  # noqa: E402
import yaml  # noqa: E402
from sqlalchemy import select, text  # noqa: E402

from app.agents import session as runner  # noqa: E402
from app.agents.llm.client import Budget  # noqa: E402
from app.agents.tools import BY_API_NAME, ToolContext, execute  # noqa: E402
from app.agents.validator import FactIndex, claims  # noqa: E402
from app.db.models.agents import AgentEvent, AgentSession  # noqa: E402
from app.db.session import async_session_factory  # noqa: E402

HERE = Path(__file__).parent
PASS_BAR = 0.9


async def latest_run(region_id: str) -> str | None:
    async with async_session_factory() as db:
        row = (
            await db.execute(
                text(
                    "select r.id from planning_run r join scenario s on s.id = r.scenario_id "
                    "where r.status = 'succeeded' and s.region_id = :r "
                    "and r.funnel->'optimisation'->>'base_id' is not null "
                    "order by r.created_at desc limit 1"
                ),
                {"r": region_id},
            )
        ).first()
    return str(row[0]) if row else None


async def run_session(case: dict[str, Any], run_id: str | None) -> dict[str, Any]:
    async with async_session_factory() as db:
        s = AgentSession(
            request=case["request"],
            status="running",
            summary={},
            region_id=case.get("region_id"),
            run_id=uuid.UUID(run_id) if run_id else None,
        )
        db.add(s)
        await db.commit()
        sid = s.id
    runner.launch(sid, runner.start_input(case["request"], case.get("region_id"), run_id))
    while True:
        await asyncio.sleep(1.0)
        if runner.is_running(sid):
            continue
        async with async_session_factory() as db:
            s = await db.get(AgentSession, sid)
            assert s is not None
            status = s.status
        if status == "awaiting_confirmation":
            runner.launch(
                sid, runner.resume_input(case.get("confirm", "approve") == "approve", None)
            )
            continue
        break
    async with async_session_factory() as db:
        s = await db.get(AgentSession, sid)
        assert s is not None
        events = (
            (
                await db.execute(
                    select(AgentEvent).where(AgentEvent.session_id == sid).order_by(AgentEvent.id)
                )
            )
            .scalars()
            .all()
        )
    return {
        "session_id": str(sid),
        "status": s.status,
        "error": s.error,
        "cost_usd": s.cost_usd,
        "llm_calls": s.llm_calls,
        "report": (s.summary or {}).get("report") or {},
        "plan": (s.summary or {}).get("plan") or {},
        "tools": [e.data.get("tool") for e in events if e.type == "tool_call"],
    }


def _at(obj: Any, path: str) -> Any:
    for part in path.split("."):
        obj = obj[part]
    return obj


async def ground_truth(fact: dict[str, Any], region_id: str | None, run_id: str | None) -> float:
    from app.main import app

    async def emit(*_: Any) -> None:
        return None

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://golden/api/v1", timeout=None
    ) as api:
        state = {k: v for k, v in {"region_id": region_id, "run_id": run_id}.items() if v}
        ctx = ToolContext(uuid.uuid4(), state, api, async_session_factory, emit, Budget(1, 1, 100))
        out = await execute(BY_API_NAME[fact["tool"].replace(".", "_")], ctx, fact["args"])
    if out.is_error or out.result is None:
        raise RuntimeError(f"ground truth {fact['tool']} failed: {out.summary}")
    return float(_at(out.result, fact["path"]))


async def check(case: dict[str, Any], result: dict[str, Any], run_id: str | None) -> list[str]:
    exp = case.get("expect", {})
    fails: list[str] = []
    report = result["report"]
    md = report.get("markdown") or ""
    low = md.lower()
    if result["status"] != "completed":
        fails.append(f"status {result['status']}: {result['error']}")
    if (report.get("validation") or {}).get("unmatched_after", 1) != 0:
        fails.append("report has unvalidated numbers")
    if "intent" in exp and (result["plan"] or {}).get("intent") != exp["intent"]:
        fails.append(f"intent {(result['plan'] or {}).get('intent')} != {exp['intent']}")
    called = set(result["tools"])
    if exp.get("tools_any") and not called & set(exp["tools_any"]):
        fails.append(f"none of {exp['tools_any']} called")
    missing = set(exp.get("tools_all", [])) - called
    if missing:
        fails.append(f"not called: {sorted(missing)}")
    forbidden = set(exp.get("tools_none", [])) & called
    if forbidden:
        fails.append(f"called forbidden: {sorted(forbidden)}")
    for group in exp.get("mentions", []):
        if not any(str(word).lower() in low for word in group):
            fails.append(f"mentions none of {group}")
    for word in exp.get("mentions_none", []):
        if word.lower() in low:
            fails.append(f"mentions forbidden '{word}'")
    for fact in exp.get("facts", []):
        try:
            truth = await ground_truth(fact, case.get("region_id"), run_id)
        except Exception as exc:
            fails.append(str(exc))
            continue
        index = FactIndex([truth])
        if not any(index.matches(c, 0.005) for c in claims(md)):
            fails.append(f"fact {fact['tool']}:{fact['path']}={truth} not stated")
    if result["cost_usd"] > exp.get("max_usd", 1.5):
        fails.append(f"cost ${result['cost_usd']:.2f} over ${exp.get('max_usd', 1.5)}")
    return fails


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", default="")
    parser.add_argument("--skip-plans", action="store_true")
    parser.add_argument("--max-usd", type=float, default=15.0, help="stop when spend exceeds")
    args = parser.parse_args()
    if not runner.llm_configured():
        print("ANTHROPIC_API_KEY is not set; golden agent evals need the Claude API.")
        return 2
    cases = yaml.safe_load((HERE / "cases.yaml").read_text())["cases"]
    wanted = {c for c in args.cases.split(",") if c}
    if wanted:
        cases = [c for c in cases if c["id"] in wanted]
    if args.skip_plans:
        cases = [c for c in cases if not c["id"].startswith("plan")]
    runs = {r: await latest_run(r) for r in ("pmr", "mumbai_pune")}
    rows, spent = [], 0.0
    for case in cases:
        run_id = runs.get(case["context_run"]) if case.get("context_run") else None
        if case.get("context_run") and not run_id:
            rows.append(
                {
                    "id": case["id"],
                    "passed": False,
                    "fails": [f"no optimised {case['context_run']} run to ask about"],
                }
            )
            continue
        started = time.monotonic()
        result = await run_session(case, run_id)
        fails = await check(case, result, run_id)
        spent += result["cost_usd"]
        rows.append(
            {
                "id": case["id"],
                "passed": not fails,
                "fails": fails,
                "seconds": round(time.monotonic() - started),
                "cost_usd": round(result["cost_usd"], 3),
                "tools": result["tools"],
                "session_id": result["session_id"],
            }
        )
        mark = "PASS" if not fails else "FAIL"
        print(
            f"{mark} {case['id']:<28} ${result['cost_usd']:.2f} "
            f"{rows[-1]['seconds']}s {'; '.join(fails)}",
            flush=True,
        )
        if spent > args.max_usd:
            print(f"Stopping: spend ${spent:.2f} exceeded --max-usd {args.max_usd}")
            break
    passed = sum(r["passed"] for r in rows)
    rate = passed / len(rows) if rows else 0.0
    print(f"\n{passed}/{len(rows)} passed ({rate:.0%}), spend ${spent:.2f}; bar {PASS_BAR:.0%}")
    out = HERE / "results"
    out.mkdir(exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    (out / f"{stamp}.json").write_text(
        json.dumps({"rate": rate, "spend_usd": spent, "cases": rows}, indent=1)
    )
    return 0 if rate >= PASS_BAR else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

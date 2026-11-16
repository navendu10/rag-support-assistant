"""Evaluate the v8 agent on its scenarios (Session 10).

Usage:
    python scripts/evaluate_agent.py --stage v8 [--ids s01,s07]

Runs every conversation in data/golden/agent_scenarios.jsonl through the agent for its
user, then scores task success (the correctness judge), tool correctness, data isolation,
steps and latency. Writes results/<stage>/agent_results.jsonl and agent_report.md.
With --ids, only those scenarios are run, and the stored results for the others are kept.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date
from pathlib import Path

from rag_assistant.agent.graph import AgentPipeline
from rag_assistant.config import get_settings, load_prompts, load_stage
from rag_assistant.eval.agent_eval import (
    ScenarioResult,
    ToolCallRecord,
    isolation_problems,
    render_report,
    tool_problems,
)
from rag_assistant.eval.judge import Judge
from rag_assistant.graph.handoff import MemoryTicketStore
from rag_assistant.llm import make_chat_model
from rag_assistant.schemas import load_agent_scenarios, load_users

SCENARIOS = Path("data/golden/agent_scenarios.jsonl")


def parse_args() -> argparse.Namespace:
    """Parse command-line options."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stage", default="v8", help="an agent stage")
    parser.add_argument("--ids", help="comma-separated scenario ids (default: all)")
    return parser.parse_args()


def order_owners(db_path: Path) -> dict[str, str]:
    """order id -> customer id, read by the evaluator (never by the agent)."""
    with sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True) as db:
        return dict(db.execute("SELECT order_id, customer_id FROM orders").fetchall())


def main() -> int:
    """Run the scenarios, score them and write the report."""
    args = parse_args()
    settings = get_settings()
    stage = load_stage(args.stage)
    if not stage.agent:
        print(f"Stage {stage.name} is not an agent stage")
        return 2
    scenarios = load_agent_scenarios(settings.paths.resolve(SCENARIOS))
    wanted = {i.strip() for i in args.ids.split(",")} if args.ids else None
    users = {u.user_id: u for u in load_users(settings.paths.resolve(settings.paths.users_file))}
    owners = order_owners(settings.paths.resolve(settings.paths.generated_dir) / "orders.db")
    judge = Judge(make_chat_model(settings, settings.llm.judge_model), load_prompts())
    out_dir = settings.paths.resolve(settings.paths.results_dir) / stage.name
    out_dir.mkdir(parents=True, exist_ok=True)
    stored_path = out_dir / "agent_results.jsonl"
    stored = {}
    if stored_path.is_file() and wanted is not None:
        stored = {
            r.id: r
            for r in (
                ScenarioResult.model_validate_json(line)
                for line in stored_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            )
        }
    results = []
    with AgentPipeline.from_stage(stage, settings, MemoryTicketStore()) as pipeline:
        for scenario in scenarios:
            if wanted is not None and scenario.id not in wanted:
                if scenario.id in stored:
                    results.append(stored[scenario.id])
                continue
            user = users[scenario.user_id]
            answer = pipeline.answer(scenario.question, user, scenario.history)
            run = answer.agent
            assert run is not None
            calls = [
                ToolCallRecord(
                    name=c.name, args=c.args, status=str(c.result.get("status")), result=c.result
                )
                for c in run.calls
            ]
            missing, forbidden = tool_problems(scenario, run.tools_used)
            isolation = isolation_problems(scenario, user, answer.text, calls, owners)
            result = ScenarioResult(
                id=scenario.id,
                topic=scenario.topic,
                user_id=scenario.user_id,
                question=scenario.question,
                answer=answer.text,
                calls=calls,
                correctness=judge.correctness(scenario, answer.text),
                tools_ok=not missing and not forbidden,
                missing_tools=missing,
                forbidden_used=forbidden,
                isolation_ok=not isolation,
                isolation_problems=isolation,
                tool_calls=run.tool_calls,
                model_calls=run.model_calls,
                generations=run.generations,
                hit_step_limit=run.hit_step_limit,
                outcome=run.outcome,
                guard_problems=run.guard_problems,
                latency_ms=run.ms,
            )
            results.append(result)
            print(
                f"{scenario.id} {scenario.topic:<26} {result.correctness.verdict or 'ERROR':<9} "
                f"tools={'ok' if result.tools_ok else 'NO'} "
                f"isolation={'ok' if result.isolation_ok else 'FAIL'} "
                f"path={' -> '.join(run.tools_used) or '-'}"
            )
    stored_path.write_text(
        "".join(r.model_dump_json() + "\n" for r in results), encoding="utf-8", newline="\n"
    )
    report = out_dir / "agent_report.md"
    report.write_text(
        render_report(stage.name, date.today().isoformat(), results), encoding="utf-8", newline="\n"
    )
    print(f"\nWrote {report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

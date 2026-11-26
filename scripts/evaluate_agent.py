"""Evaluate an agent stage on its scenarios (Session 10: v8; Session 11: v9).

Usage:
    python scripts/evaluate_agent.py --stage v8 [--ids s01,s07]
    python scripts/evaluate_agent.py --stage v9            # adds the approval scenarios

Runs every conversation in data/golden/agent_scenarios.jsonl through the agent for its
user, then scores task success, tool correctness, data isolation, steps and latency.
Scenarios with client steps (approve, reject, more messages) run only on an approval
stage, each with a fresh in-memory ticket store, and are scored on what was written:
unapproved writes, duplicate tickets and false ticket claims must all be 0. Writes
results/<stage>/agent_results.jsonl and agent_report.md. With --ids, only those scenarios
are run, and the stored results for the others are kept.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date
from pathlib import Path

from rag_assistant.agent.graph import AgentPipeline
from rag_assistant.config import get_settings, load_stage
from rag_assistant.eval.agent_eval import ScenarioResult, render_report, run_scenario
from rag_assistant.eval.runner import make_judge
from rag_assistant.graph.handoff import MemoryTicketStore
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
    judge = make_judge(settings)
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
            if scenario.steps and not stage.approval:
                continue  # approval scenarios need an approval stage (v9)
            result = run_scenario(pipeline, scenario, users, owners, judge)
            results.append(result)
            print(
                f"{scenario.id} {scenario.topic:<36} "
                f"{'ok' if result.success else 'FAIL':<5} "
                f"tools={'ok' if result.tools_ok else 'NO'} "
                f"isolation={'ok' if result.isolation_ok else 'FAIL'} "
                f"tickets={len(result.tickets)} "
                f"actions={','.join(f'{x.action}:{x.status}' for x in result.steps) or '-'} "
                f"path={' -> '.join(c.name for c in result.calls) or '-'}"
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

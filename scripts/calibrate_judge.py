"""Measure the correctness judge against the hand labels (Session 13 Part B).

Usage:
    python scripts/calibrate_judge.py --prompt judge_correctness [--runs 3]
    python scripts/calibrate_judge.py --prompt judge_correctness_v2 --compare judge_correctness

Runs the judge ``--runs`` times on every labelled answer in data/golden/judge_labels.jsonl
(golden rows and agent scenarios, with this turn's tool results as evidence), with the LLM
cache bypassed so the runs can differ. Writes results/calibration/judge_<prompt>.jsonl and
.md: agreement with the labels, Cohen's kappa, the confusion table, unstable items and
every disagreement. With --compare, also lists the items whose verdict changed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from rag_assistant.config import PROJECT_ROOT, get_settings, load_prompts
from rag_assistant.eval.agent_eval import ScenarioResult, scenario_evidence
from rag_assistant.eval.calibration import (
    CalibrationItem,
    changed_verdicts,
    summarise,
    verdict_label,
)
from rag_assistant.eval.judge import Judge
from rag_assistant.eval.labelling import LABELS_FILE, load_labels
from rag_assistant.eval.metrics import ResultRow
from rag_assistant.llm import make_chat_model
from rag_assistant.schemas import load_agent_scenarios, load_golden

OUT = PROJECT_ROOT / "results" / "calibration"


def parse_args() -> argparse.Namespace:
    """Parse command-line options."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--prompt", default="judge_correctness", help="prompt key to measure")
    parser.add_argument("--runs", type=int, default=3, help="judge runs per answer")
    parser.add_argument("--compare", help="another measured prompt key: list changed verdicts")
    return parser.parse_args()


def _rows(path: Path) -> dict[str, dict]:
    """A results JSONL file keyed by id."""
    if not path.is_file():
        return {}
    return {
        json.loads(x)["id"]: json.loads(x)
        for x in path.read_text("utf-8").splitlines()
        if x.strip()
    }


def _load(path: Path) -> list[CalibrationItem]:
    """Stored calibration items."""
    return [
        CalibrationItem(**{**d, "runs": tuple(d["runs"]), "reasons": tuple(d["reasons"])})
        for d in (json.loads(x) for x in path.read_text("utf-8").splitlines() if x.strip())
    ]


def render(prompt: str, runs: int, items: list[CalibrationItem], changed: list | None) -> str:
    """The calibration report."""
    s = summarise(items)
    c = s.confusion
    lines = [
        f"# Judge calibration: `{prompt}`",
        "",
        f"- {s.items} hand-labelled answers, {runs} judge runs each (cache bypassed); the "
        "verdict is the majority of the runs.",
        f"- **Agreement with the labels: {s.agreement:.1%}**; **Cohen's kappa: "
        f"{'undefined' if s.kappa is None else s.kappa}**; unstable items (runs disagreed): "
        f"{s.unstable}.",
        "",
        "| Label \\ judge | pass | fail |",
        "|---|---|---|",
        f"| pass | {c['pass->pass']} | {c['pass->fail']} |",
        f"| fail | {c['fail->pass']} | {c['fail->fail']} |",
        "",
        "## Disagreements",
        "",
        "| Stage | Item | Label | Judge runs | Judge's reason (first run) |",
        "|---|---|---|---|---|",
    ]
    for i in s.disagreements:
        reason = (i.reasons[0] if i.reasons else "").replace("|", "\\|")
        lines.append(f"| {i.stage} | {i.item_id} | {i.label} | {', '.join(i.runs)} | {reason} |")
    if changed is not None:
        lines += ["", "## Verdicts that changed", "", "| Stage | Item | Before | After |",
                  "|---|---|---|---|"]  # fmt: skip
        lines += [f"| {st} | {it} | {a} | {b} |" for st, it, a, b in changed] or [
            "| - | - | - | - |"
        ]
    return "\n".join(lines) + "\n"


def main() -> int:
    """Judge every labelled answer ``--runs`` times and write the report."""
    args = parse_args()
    settings = get_settings()
    model = make_chat_model(settings, settings.llm.judge_model)
    model.cache = False  # each run is a real call, so variance can show
    judge = Judge(model, load_prompts(), args.prompt)
    golden = {i.id: i for i in load_golden(settings.paths.resolve(settings.paths.golden_file))}
    scenarios = {
        s.id: s for s in load_agent_scenarios(PROJECT_ROOT / "data/golden/agent_scenarios.jsonl")
    }
    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / f"judge_{args.prompt}.jsonl"
    done = {(i.stage, i.item_id): i for i in _load(out)} if out.is_file() else {}
    items = []
    for label in load_labels(PROJECT_ROOT / LABELS_FILE):
        key = (label.stage, label.item_id)
        if key in done:
            items.append(done[key])
            continue
        stage_dir = PROJECT_ROOT / "results" / label.stage
        if label.kind == "scenario":
            row = ScenarioResult.model_validate(
                _rows(stage_dir / "agent_results.jsonl")[label.item_id]
            )
            item, answer, evidence = (
                scenarios[label.item_id],
                row.answer,
                scenario_evidence(row.calls),
            )
        else:
            row = ResultRow.model_validate(_rows(stage_dir / "results.jsonl")[label.item_id])
            item, answer = golden[label.item_id], row.answer
            facts = row.agent.tool_facts if row.agent else []
            evidence = "\n".join(f"- {f}" for f in facts)
        judgements = [judge.correctness(item, answer, evidence) for _ in range(args.runs)]
        result = CalibrationItem(
            stage=label.stage,
            item_id=label.item_id,
            label=label.label,
            runs=tuple(verdict_label(j.verdict) for j in judgements),
            reasons=tuple(j.reason for j in judgements),
        )
        items.append(result)
        with out.open("a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps({**result.__dict__, "runs": list(result.runs),
                                "reasons": list(result.reasons)}) + "\n")  # fmt: skip
        print(f"{label.stage:<4} {label.item_id} label={label.label} runs={','.join(result.runs)}")
    changed = None
    if args.compare:
        changed = changed_verdicts(_load(OUT / f"judge_{args.compare}.jsonl"), items)
    report = OUT / f"judge_{args.prompt}.md"
    report.write_text(
        render(args.prompt, args.runs, items, changed), encoding="utf-8", newline="\n"
    )
    s = summarise(items)
    print(f"\nagreement {s.agreement:.1%}, kappa {s.kappa}, unstable {s.unstable}; wrote {report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

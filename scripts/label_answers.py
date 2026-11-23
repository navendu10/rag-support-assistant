"""Label stored answers by hand for judge calibration (Session 13). No model is called.

Usage:
    python scripts/label_answers.py            # resume where you stopped
    python scripts/label_answers.py --list     # show the 40 items and which are labelled

Shows 40 stored answers, one at a time: every item on the judge calibration list in
docs/evaluation.md (golden rows and agent scenarios, with their stage), then a fixed
random sample of golden rows from v4-v10. For each: the question (and earlier turns), the
reference answer, the stored answer and its sources (the passages it cited, with doc id
and trust; for agent scenarios, the tool calls and client decisions). Type p (pass),
f (fail), s (skip) or q (quit), then an optional note. Labels are appended to
data/golden/judge_labels.jsonl; already labelled items are skipped.
"""

from __future__ import annotations

import argparse
import json
import sys
import textwrap
from pathlib import Path

from rag_assistant.config import PROJECT_ROOT, get_settings, load_stage
from rag_assistant.eval.contexts import chunk_lookup, stored_context
from rag_assistant.eval.labelling import (
    LABELS_FILE,
    ItemRef,
    Label,
    append_label,
    calibration_refs,
    load_labels,
    select_items,
    stored_refs,
    unlabelled,
)
from rag_assistant.eval.metrics import ResultRow

WIDTH = 96


def parse_args() -> argparse.Namespace:
    """Parse command-line options."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--list", action="store_true", help="list the items and exit")
    return parser.parse_args()


def _wrap(label: str, text: str) -> str:
    """``label: text`` wrapped to the terminal width."""
    return textwrap.fill(text, WIDTH, initial_indent=f"{label}: ", subsequent_indent="  ")


def _jsonl(path: Path) -> dict[str, dict]:
    """A JSONL file keyed by ``id``."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    return {json.loads(x)["id"]: json.loads(x) for x in lines if x.strip()}


def show_golden(ref: ItemRef, results: Path, references: dict[str, dict]) -> None:
    """A golden row: question, reference, answer and the passages it cited."""
    row = ResultRow.model_validate(_jsonl(results / ref.stage / "results.jsonl")[ref.item_id])
    item = references[ref.item_id]
    for turn in item.get("history", []):
        print(_wrap(f"  earlier {turn['role']}", turn["content"]))
    print(_wrap("Question", f"{row.question}  ({row.user_id}, {row.failure_tag})"))
    print(_wrap("Reference", item["reference_answer"]))
    print(_wrap("Answer", row.answer))
    settings = get_settings()
    lookup = chunk_lookup(
        load_stage(ref.stage), settings.paths.resolve(settings.paths.generated_dir)
    )
    context = stored_context(row, lookup)
    cited = row.grounded.citations if row.grounded and row.grounded.citations else []
    numbers = cited or list(range(1, len(context) + 1))
    print("Sources" + (" (cited):" if cited else " (context):"))
    for n in numbers:
        if 1 <= n <= len(context):
            c = context[n - 1]
            print(textwrap.fill(f"[{n}] {c.doc_id}#{c.chunk_index} ({c.trust}): {c.text[:420]}",
                                WIDTH, subsequent_indent="      "))  # fmt: skip
    if not numbers:
        print("  (none)")


def show_scenario(ref: ItemRef, results: Path, scenarios: dict[str, dict]) -> None:
    """An agent scenario: question, reference, answer, tool calls and decisions."""
    row = _jsonl(results / ref.stage / "agent_results.jsonl")[ref.item_id]
    print(_wrap("Question", f"{row['question']}  ({row['user_id']}, {row['topic']})"))
    print(_wrap("Reference", scenarios[ref.item_id]["reference_answer"]))
    print(_wrap("Answer", row["answer"]))
    print("Tool calls:")
    for c in row["calls"]:
        print(textwrap.fill(f"  {c['name']}({c['args']}) -> {c['status']}: "
                            f"{json.dumps(c['result'], ensure_ascii=False)[:300]}",
                            WIDTH, subsequent_indent="      "))  # fmt: skip
    for s in row.get("steps", []):
        print(_wrap(f"  then {s['action']} -> {s['status']}", s["text"][:300]))


def main() -> int:
    """Show each unlabelled item and record the label."""
    args = parse_args()
    results = PROJECT_ROOT / "results"
    calibration = calibration_refs((PROJECT_ROOT / "docs" / "evaluation.md").read_text("utf-8"))
    items = select_items(calibration, stored_refs(results))
    labels_path = PROJECT_ROOT / LABELS_FILE
    labels = load_labels(labels_path)
    todo = unlabelled(items, labels)
    if args.list:
        for n, ref in enumerate(items, start=1):
            mark = " " if ref in todo else "x"
            tag = "calibration" if ref in calibration else "sample"
            print(f"[{mark}] {n:2d}. {ref.stage:<4} {ref.item_id}  {ref.kind:<8} {tag}")
        return 0
    references = _jsonl(PROJECT_ROOT / "data" / "golden" / "questions.jsonl")
    scenarios = _jsonl(PROJECT_ROOT / "data" / "golden" / "agent_scenarios.jsonl")
    print(
        f"{len(items) - len(todo)}/{len(items)} labelled. p = pass, f = fail, s = skip, q = quit."
    )
    for ref in todo:
        print("\n" + "=" * WIDTH)
        print(f"{items.index(ref) + 1}/{len(items)}  stage {ref.stage}  {ref.item_id}"
              f"{'  (calibration list)' if ref in calibration else ''}")  # fmt: skip
        (show_scenario if ref.kind == "scenario" else show_golden)(
            ref, results, scenarios if ref.kind == "scenario" else references
        )
        choice = ""
        while choice not in {"p", "f", "s", "q"}:
            choice = input("Label [p/f/s/q]: ").strip().lower()[:1]
        if choice == "q":
            return 0
        if choice == "s":
            continue
        note = input("Note (optional): ").strip()
        label = "pass" if choice == "p" else "fail"
        append_label(labels_path, Label(stage=ref.stage, item_id=ref.item_id, kind=ref.kind,
                                        label=label, note=note))  # fmt: skip
    print(f"\nDone: {len(load_labels(labels_path))} labels in {LABELS_FILE}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

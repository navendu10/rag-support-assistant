"""Before/after table across every evaluated stage (results/*/summary.json)."""

from __future__ import annotations

import json
from pathlib import Path

from rag_assistant.eval.metrics import format_rate

COLUMNS = (
    ("recall_at_k", "Recall@5"),
    ("correctness", "Correctness"),
    ("retrieval_leaks", "Retrieval leaks"),
    ("answer_leaks", "Answer leaks"),
    ("injections", "Injection failures"),
    ("handoffs", "Handoffs"),
)


def load_summaries(results_dir: Path) -> list[dict]:
    """Every full-run summary under ``results_dir``, ordered by stage name."""
    paths = sorted(results_dir.glob("*/summary.json"), key=lambda p: p.parent.name)
    return [json.loads(p.read_text(encoding="utf-8")) for p in paths]


def comparison_table(summaries: list[dict]) -> str:
    """Markdown table with one row per stage."""
    header = "| Stage | " + " | ".join(label for _, label in COLUMNS) + " |"
    lines = [header, "|---|" + "---|" * len(COLUMNS)]
    for s in summaries:
        cells = " | ".join(format_rate(s["overall"][key]) for key, _ in COLUMNS)
        lines.append(f"| {s['meta']['stage']} | {cells} |")
    return "\n".join(lines)

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


TAG_METRICS = (
    ("correctness", "Correct"),
    ("recall_at_k", "Recall@5"),
    ("forbidden_phrases", "Forbidden phrase"),
)


def _count(rate: dict[str, int]) -> str:
    """Compact ``k/n`` for per-tag tables (n/a when nothing was eligible)."""
    return f"{rate['count']}/{rate['total']}" if rate["total"] else "n/a"


def tag_comparison_table(summaries: list[dict]) -> str:
    """Markdown table: one row per failure tag, one column per stage and metric."""
    stages = [s["meta"]["stage"] for s in summaries]
    tags = list(dict.fromkeys(tag for s in summaries for tag in s["by_tag"]))
    header = "| Tag | n | " + " | ".join(
        f"{label} {stage}" for _, label in TAG_METRICS for stage in stages
    )
    lines = [header + " |", "|---|---|" + "---|" * (len(TAG_METRICS) * len(stages))]
    for tag in tags:
        n = next(s["by_tag"][tag]["questions"] for s in summaries if tag in s["by_tag"])
        cells = [
            _count(s["by_tag"][tag][key]) if tag in s["by_tag"] else "-"
            for key, _ in TAG_METRICS
            for s in summaries
        ]
        lines.append(f"| {tag} | {n} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def comparison_table(summaries: list[dict]) -> str:
    """Markdown table with one row per stage."""
    header = "| Stage | " + " | ".join(label for _, label in COLUMNS) + " |"
    lines = [header, "|---|" + "---|" * len(COLUMNS)]
    for s in summaries:
        cells = " | ".join(format_rate(s["overall"][key]) for key, _ in COLUMNS)
        lines.append(f"| {s['meta']['stage']} | {cells} |")
    return "\n".join(lines)

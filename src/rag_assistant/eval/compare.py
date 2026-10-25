"""Before/after table across every evaluated stage (results/*/summary.json)."""

from __future__ import annotations

import json
from pathlib import Path

from rag_assistant.eval.metrics import format_metric

COLUMNS = (
    ("recall_at_k", "Recall@5"),
    ("hit_at_1", "Hit@1"),
    ("mrr", "MRR"),
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
    ("hit_at_1", "Hit@1"),
    ("mrr", "MRR"),
    ("forbidden_phrases", "Forbidden phrase"),
)


def _cell(value: dict | None) -> str:
    """Compact per-tag cell: ``k/n`` for rates, ``0.82`` for means, n/a or - when missing."""
    if value is None:
        return "-"
    if not value["total"]:
        return "n/a"
    if "value" in value:
        return f"{value['value']:.2f}"
    return f"{value['count']}/{value['total']}"


def tag_metric_table(summaries: list[dict], key: str, label: str) -> str:
    """Markdown table for one metric: one row per failure tag, one column per stage."""
    stages = [s["meta"]["stage"] for s in summaries]
    tags = list(dict.fromkeys(tag for s in summaries for tag in s["by_tag"]))
    lines = [
        f"| {label} by tag | n | " + " | ".join(stages) + " |",
        "|---|---|" + "---|" * len(stages),
    ]
    for tag in tags:
        n = next(s["by_tag"][tag]["questions"] for s in summaries if tag in s["by_tag"])
        cells = [_cell(s["by_tag"].get(tag, {}).get(key)) for s in summaries]
        lines.append(f"| {tag} | {n} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def tag_comparison_table(summaries: list[dict]) -> str:
    """One per-tag table for each metric in ``TAG_METRICS``, separated by blank lines."""
    return "\n\n".join(tag_metric_table(summaries, key, label) for key, label in TAG_METRICS)


def comparison_table(summaries: list[dict]) -> str:
    """Markdown table with one row per stage (``-`` for a metric a summary predates)."""
    header = "| Stage | " + " | ".join(label for _, label in COLUMNS) + " |"
    lines = [header, "|---|" + "---|" * len(COLUMNS)]
    for s in summaries:
        overall = s["overall"]
        cells = " | ".join(
            format_metric(overall[key]) if key in overall else "-" for key, _ in COLUMNS
        )
        lines.append(f"| {s['meta']['stage']} | {cells} |")
    return "\n".join(lines)

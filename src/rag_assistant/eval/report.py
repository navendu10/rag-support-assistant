"""Render an evaluation run as Markdown."""

from __future__ import annotations

from collections.abc import Sequence

from rag_assistant.eval.metrics import ResultRow, format_rate

OVERALL_ROWS = (
    ("recall_at_k", "Recall@{k} (any expected doc in top {k})", "higher"),
    ("correctness", "Correctness (judge: CORRECT)", "higher"),
    ("retrieval_leaks", "Retrieval leaks (internal chunk in a customer's context)", "lower"),
    ("answer_leaks", "Answer leaks (leak items: forbidden phrase in answer)", "lower"),
    ("injections", "Injection failures (injection items)", "lower"),
    ("handoffs", "Handoffs (expect_handoff items: judge HANDOFF)", "higher"),
    ("forbidden_phrases", "Answers containing a must_not_contain phrase", "lower"),
)

TAG_COLUMNS = (
    ("recall_at_k", "Recall@{k}"),
    ("correctness", "Correct"),
    ("retrieval_leaks", "Retrieval leaks"),
    ("forbidden_phrases", "Forbidden phrase"),
    ("handoffs", "Handoff"),
)


def _cell(text: object) -> str:
    """Make a value safe for a Markdown table cell."""
    return str(text).replace("|", "\\|").replace("\n", " ")


def _flag(value: bool | None, yes: str = "yes", no: str = "-") -> str:
    """Render a tri-state flag."""
    if value is None:
        return "n/a"
    return yes if value else no


def overall_table(summary: dict[str, object], k: int) -> str:
    """Headline metrics."""
    lines = ["| Metric | Result | Better |", "|---|---|---|"]
    for key, label, better in OVERALL_ROWS:
        lines.append(f"| {label.format(k=k)} | {format_rate(summary[key])} | {better} |")
    latency = summary["latency_ms"]
    lines.append(
        f"| Latency p50 / p95 (ms) | {latency['p50']:.0f} / {latency['p95']:.0f} | lower |"
    )
    lines.append(f"| Judge errors (unparseable verdicts) | {summary['judge_errors']} | lower |")
    return "\n".join(lines)


def tag_table(by_tag: dict[str, dict[str, object]], k: int) -> str:
    """Per-failure-tag metrics."""
    header = "| Tag | n | " + " | ".join(label.format(k=k) for _, label in TAG_COLUMNS) + " |"
    lines = [header, "|---|---|" + "---|" * len(TAG_COLUMNS)]
    for tag, s in by_tag.items():
        cells = " | ".join(format_rate(s[key]) for key, _ in TAG_COLUMNS)
        lines.append(f"| {tag} | {s['questions']} | {cells} |")
    return "\n".join(lines)


def item_table(rows: Sequence[ResultRow]) -> str:
    """One line per question, with the judge's reason for spot-checking."""
    lines = [
        "| id | tag | recall hit | verdict | handoff | ret. leak | forbidden hits | judge reason |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        handoff = r.handoff.verdict if r.handoff else "n/a"
        lines.append(
            f"| {r.id} | {r.failure_tag} | {_flag(r.checks.recall_hit, 'hit', 'MISS')} | "
            f"{r.correctness.verdict or 'ERROR'} | {handoff} | "
            f"{_flag(r.checks.retrieval_leak, 'LEAK')} | "
            f"{_cell(', '.join(r.checks.forbidden_hits) or '-')} | {_cell(r.correctness.reason)} |"
        )
    return "\n".join(lines)


def render_report(meta: dict[str, object], summary: dict, by_tag: dict, rows: list) -> str:
    """The full report.md text."""
    k = int(meta["recall_k"])
    subset = meta.get("filters") or "none (full golden set)"
    return (
        "\n\n".join(
            [
                f"# Evaluation report: stage `{meta['stage']}`",
                "\n".join(
                    [
                        f"- Date: {meta['date']}",
                        f"- Questions: {summary['questions']} (filters: {subset})",
                        f"- Generation model: `{meta['generation_model']}`; judge model: "
                        f"`{meta['judge_model']}`; reasoning effort: `{meta['reasoning_effort']}`",
                        f"- Context chunks (top_k): {meta['top_k']}; recall measured at k={k}",
                        "- Metric definitions: [docs/evaluation.md](../../docs/evaluation.md)",
                    ]
                ),
                "## Overall",
                overall_table(summary, k),
                "## By failure tag",
                tag_table(by_tag, k),
                "## Per question",
                item_table(rows),
            ]
        )
        + "\n"
    )

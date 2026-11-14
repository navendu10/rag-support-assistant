"""Render an evaluation run as Markdown."""

from __future__ import annotations

from collections.abc import Sequence

from rag_assistant.eval.metrics import ResultRow, false_handoff_rows, format_metric, miss_type

OVERALL_ROWS = (
    ("recall_at_k", "Recall@{k} (any expected doc in top {k})", "higher"),
    ("hit_at_1", "Hit@1 (an expected doc ranked first)", "higher"),
    ("mrr", "MRR (mean 1/rank of the first expected doc in top {k}, 0 if absent)", "higher"),
    ("correctness", "Correctness (judge: CORRECT)", "higher"),
    ("retrieval_leaks", "Retrieval leaks (internal chunk in a customer's context)", "lower"),
    ("answer_leaks", "Answer leaks, string check (leak items: forbidden phrase)", "lower"),
    ("answer_leaks_judged", "Answer leaks, LLM judge (leak items: reveals internal info)", "lower"),
    ("injections", "Injection failures (injection items)", "lower"),
    (
        "handoffs_route",
        "Handoffs by route (expect_handoff items: the flow handed off; v6 on)",
        "higher",
    ),
    ("handoffs", "Handoffs, judge's view (expect_handoff items: judge HANDOFF)", "higher"),
    ("forbidden_phrases", "Answers containing a must_not_contain phrase", "lower"),
)
# Rows shown only when the run has eligible items (agents with internal docs; v5 on).
OPTIONAL_ROWS = (
    (
        "agent_internal_access",
        "Agent internal access (agent items on playbooks: internal doc in context AND correct)",
        "higher",
    ),
    (
        "agent_internal_retrieval",
        "Agent internal retrieval (agent items on playbooks: internal doc in top 5)",
        "higher",
    ),
    (
        "agent_internal_context",
        "Agent internal context (internal doc in an agent's context)",
        "higher",
    ),
    (
        "security_events",
        "Security events (internal chunk dropped for a customer; must be 0)",
        "lower",
    ),
    (
        "false_handoffs",
        "False handoffs (answerable questions handed off; v6: the flow, else the judge)",
        "lower",
    ),
    (
        "invented_content",
        "Invented content (judge: the answer states something its sources don't support)",
        "lower",
    ),
    ("citations_valid", "Citations valid (grounded replies whose citations all exist)", "higher"),
    (
        "grounded_reattempts",
        "Grounded replies retried (first structured reply invalid)",
        "lower",
    ),
    ("flow_retries", "Corrective flow: broader retries (grade below min_score)", "lower"),
    ("flow_retries_answerable", "Corrective flow: retries on answerable questions", "lower"),
    ("flow_handoffs", "Corrective flow: handoffs (all questions)", "n/a"),
    ("retries_low_score", "Corrective flow: retries triggered by a low score", "n/a"),
    ("retries_not_found", "Corrective flow: retries triggered by a not-found answer", "n/a"),
    ("guard_triggers", "Output guard: answers rejected at least once (v7)", "lower"),
    (
        "guard_triggers_answerable",
        "Output guard: rejections on answerable questions (a cost on good answers)",
        "lower",
    ),
    ("guard_handoffs", "Output guard: handoffs after a second rejection", "lower"),
    (
        "retries_rescued",
        "Corrective flow: not-found retries that ended in a CORRECT answer",
        "higher",
    ),
)

TAG_COLUMNS = (
    ("recall_at_k", "Recall@{k}"),
    ("hit_at_1", "Hit@1"),
    ("mrr", "MRR"),
    ("correctness", "Correct"),
    ("retrieval_leaks", "Retrieval leaks"),
    ("forbidden_phrases", "Forbidden phrase"),
    ("handoffs_route", "Handoff (route)"),
    ("handoffs", "Handoff (judge)"),
)


def _cell(text: object) -> str:
    """Make a value safe for a Markdown table cell."""
    return str(text).replace("|", "\\|").replace("\n", " ")


def _flag(value: bool | None, yes: str = "yes", no: str = "-") -> str:
    """Render a tri-state flag."""
    if value is None:
        return "n/a"
    return yes if value else no


def _rank(reciprocal: float | None) -> str:
    """Rank of the first expected doc from its reciprocal (``-`` if absent, n/a if none)."""
    if reciprocal is None:
        return "n/a"
    return str(round(1 / reciprocal)) if reciprocal else "-"


def step_latency_rows(latency: dict) -> list[str]:
    """One table row per measured pipeline step (mean / p95, ms)."""
    waits = latency.get("generate_excludes_waits", False)
    labels = {
        "rewrite": "Rewrite latency mean / p95 (ms), uncached calls, throttle waits excluded",
        "rewrite_wait": "Throttle and retry waits during rewriting, mean / p95 (ms)",
        "retrieve": "Retrieval latency mean / p95 (ms)",
        "rerank": "Rerank latency mean / p95 (ms)",
        "retry_retrieve": "Retry search latency mean / p95 (ms), questions that retried",
        "retry_rerank": "Retry rerank latency mean / p95 (ms), questions that retried",
        "regenerate": "Regeneration after a not-found retry, mean / p95 (ms), uncached",
        "generate": (
            "Generation latency mean / p95 (ms), uncached calls, throttle waits excluded"
            if waits
            else "Generation latency mean / p95 (ms), includes throttle waits and cache hits"
        ),
        "generate_wait": (
            "Throttle and retry waits during generation, mean / p95 (ms); a few long waits "
            "give a high mean with a low p95"
        ),
    }
    rows = []
    for step, label in labels.items():
        stats = latency.get(step)
        if isinstance(stats, dict) and stats.get("n"):
            counted = step == "rewrite" or (step == "generate" and waits)
            suffix = f" (n={stats['n']})" if counted else ""
            rows.append(f"| {label}{suffix} | {stats['mean']:.0f} / {stats['p95']:.0f} | lower |")
    return rows


def overall_table(summary: dict[str, object], k: int) -> str:
    """Headline metrics."""
    lines = ["| Metric | Result | Better |", "|---|---|---|"]
    for key, label, better in OVERALL_ROWS:
        if key in summary:
            lines.append(f"| {label.format(k=k)} | {format_metric(summary[key])} | {better} |")
    for key, label, better in OPTIONAL_ROWS:
        value = summary.get(key, {"count": 0, "total": 0})
        if value["total"]:
            lines.append(f"| {label} | {format_metric(value)} | {better} |")
    candidates = summary.get("candidate_recall", {"count": 0, "total": 0})
    if candidates["total"]:
        lines.append(
            f"| Candidate recall (any expected doc among the reranker's fetch_k inputs) | "
            f"{format_metric(candidates)} | higher |"
        )
    for key, label in (
        ("rewrite_fallbacks", "Rewrite fallbacks (invalid output twice: raw message used)"),
        ("rewrite_flagged", "Rewrites flagged (invented product, dropped code, added country)"),
    ):
        value = summary.get(key, {"count": 0, "total": 0})
        if value["total"]:
            lines.append(f"| {label} | {format_metric(value)} | lower |")
    latency = summary["latency_ms"]
    lines.append(
        f"| Total latency p50 / p95 (ms), wall clock, throttle waits and cache hits included | "
        f"{latency['p50']:.0f} / {latency['p95']:.0f} | lower |"
    )
    lines += step_latency_rows(latency)
    lines.append(f"| Judge errors (unparseable verdicts) | {summary['judge_errors']} | lower |")
    return "\n".join(lines)


def tag_table(by_tag: dict[str, dict[str, object]], k: int) -> str:
    """Per-failure-tag metrics."""
    header = "| Tag | n | " + " | ".join(label.format(k=k) for _, label in TAG_COLUMNS) + " |"
    lines = [header, "|---|---|" + "---|" * len(TAG_COLUMNS)]
    for tag, s in by_tag.items():
        cells = " | ".join(format_metric(s[key]) for key, _ in TAG_COLUMNS)
        lines.append(f"| {tag} | {s['questions']} | {cells} |")
    return "\n".join(lines)


def item_table(rows: Sequence[ResultRow]) -> str:
    """One line per question, with the judge's reason for spot-checking."""
    lines = [
        "| id | tag | recall hit | rank | expected doc | verdict | handoff | ret. leak "
        "| forbidden hits | judge reason |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        handoff = r.handoff.verdict if r.handoff else "n/a"
        lines.append(
            f"| {r.id} | {r.failure_tag} | {_flag(r.checks.recall_hit, 'hit', 'MISS')} | "
            f"{_rank(r.checks.reciprocal_rank)} | {miss_type(r) or 'n/a'} | "
            f"{r.correctness.verdict or 'ERROR'} | {handoff} | "
            f"{_flag(r.checks.retrieval_leak, 'LEAK')} | "
            f"{_cell(', '.join(r.checks.forbidden_hits) or '-')} | {_cell(r.correctness.reason)} |"
        )
    return "\n".join(lines)


def rewrite_table(rows: Sequence[ResultRow]) -> str:
    """Rewrites that were flagged or fell back, for spot checks (all rows: show_rewrites.py)."""
    lines = [
        "| id | fallback | flags | standalone question | search query |",
        "|---|---|---|---|---|",
    ]
    for r in rows:
        rw = r.rewrite
        if rw is not None and (rw.flags or rw.fallback):
            fallback = "raw message" if rw.fallback else "-"
            lines.append(
                f"| {r.id} | {fallback} | "
                f"{_cell(', '.join(r.rewrite.flags) or '-')} | "
                f"{_cell(r.rewrite.standalone_question)} | {_cell(r.rewrite.search_query)} |"
            )
    if len(lines) == 2:
        return "No rewrite was flagged or fell back."
    return "\n".join(lines)


def false_handoff_table(rows: Sequence[ResultRow]) -> str:
    """Every answerable question that was handed off, with why."""
    lines = ["| id | tag | why | answer |", "|---|---|---|---|"]
    for r in false_handoff_rows(rows):
        if r.path is not None:
            why = f"flow: {r.path.handoff_reason} (grade {r.path.grade})"
        else:
            why = f"judge: {r.handoff.reason if r.handoff else '-'}"
        lines.append(f"| {r.id} | {r.failure_tag} | {_cell(why)} | {_cell(r.answer[:160])} |")
    if len(lines) == 2:
        return "No answerable question was handed off."
    return "\n".join(lines)


def _score(value: float | None) -> str:
    """A 0-1 score with three decimals, or ``-``."""
    return "-" if value is None else f"{value:.3f}"


def flow_table(rows: Sequence[ResultRow]) -> str:
    """The corrective flow's route for every question (v6)."""
    lines = [
        "| id | tag | best score | grade | retry trigger | retry (best, kept) "
        "| changed outcome | handoff | ticket |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        p = r.path
        if p is None:
            continue
        retry = f"{_score(p.retry_best_score)}, {'kept' if p.retry_kept else 'not kept'}"
        lines.append(
            f"| {r.id} | {r.failure_tag} | {_score(p.best_score)} | {p.grade} | "
            f"{p.retry_trigger} | {retry if p.retry_used else '-'} | "
            f"{_flag(p.retry_changed_outcome)} | {p.handoff_reason or '-'} | "
            f"{'yes' if p.ticket_created else '-'} |"
        )
    return "\n".join(lines)


def invented_table(rows: Sequence[ResultRow]) -> str:
    """Answers the invented-content judge flagged, with its reason."""
    lines = ["| id | tag | judge reason |", "|---|---|---|"]
    for r in rows:
        if r.invented is not None and r.invented.verdict != "SUPPORTED":
            lines.append(f"| {r.id} | {r.failure_tag} | {_cell(r.invented.reason)} |")
    if len(lines) == 2:
        return "No answer was judged to invent content."
    return "\n".join(lines)


def render_report(meta: dict[str, object], summary: dict, by_tag: dict, rows: list) -> str:
    """The full report.md text."""
    k = int(meta["recall_k"])
    subset = meta.get("filters") or "none (full golden set)"
    rewrites = (
        ["## Query rewrites flagged or fallen back", rewrite_table(rows)]
        if any(r.rewrite is not None for r in rows)
        else []
    )
    handoffs = (
        ["## False handoffs (answerable questions handed off)", false_handoff_table(rows)]
        if summary.get("false_handoffs", {}).get("total")
        else []
    )
    invented = (
        ["## Invented content (judge)", invented_table(rows)]
        if any(r.invented is not None for r in rows)
        else []
    )
    flow = (
        ["## Corrective flow: route per question", flow_table(rows)]
        if any(r.path is not None for r in rows)
        else []
    )
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
                *rewrites,
                *handoffs,
                *invented,
                *flow,
                "## Per question",
                item_table(rows),
            ]
        )
        + "\n"
    )

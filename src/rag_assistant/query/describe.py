"""Human-readable lines describing how a question was handled (for ask.py and chat.py)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rag_assistant.pipeline import Answer


def describe_rewrite(answer: Answer) -> list[str]:
    """The rewrite (if any) and the region filter that were applied."""
    lines = []
    if answer.rewrite is not None:
        r = answer.rewrite.rewrite
        fallback = f"  (FALLBACK: {answer.rewrite.error})" if answer.rewrite.fallback else ""
        lines += [
            f"standalone : {r.standalone_question}{fallback}",
            f"search     : {r.search_query}",
            f"codes      : {', '.join(r.product_codes) or '-'}   "
            f"category: {r.product_category or '-'}",
        ]
    regions = answer.region_filter
    lines.append(f"filter     : {'region in ' + ', '.join(regions) if regions else 'none'}")
    return lines


def describe_retrieval(answer: Answer) -> list[str]:
    """One line per retrieved chunk: rank, doc_id#chunk, region, and whether it is context."""
    in_context = len(answer.context)
    return [
        f"  {n}. {c.doc_id}#{c.chunk_index}  ({c.region}, {c.audience})"
        + ("" if n <= in_context else "  [not in context]")
        for n, c in enumerate(answer.retrieved, start=1)
    ]


def describe_path(answer: Answer) -> list[str]:
    """Grounding and the corrective flow (Session 8): found or not, the grade, any retry
    and any handoff. Empty for stages without grounding."""
    lines = []
    g = answer.grounded
    if g is not None:
        status = "valid" if g.valid else f"INVALID after {g.attempts} attempts: {g.error}"
        lines.append(
            f"grounded   : found_in_sources={g.found_in_sources}, citations "
            f"{list(g.citations) or '-'} ({status})"
        )
    p = answer.path
    if p is not None:
        best = "-" if p.best_score is None else f"{p.best_score:.3f}"
        retry = "no retry"
        if p.retry_used:
            retry_best = "-" if p.retry_best_score is None else f"{p.retry_best_score:.3f}"
            kept = "kept" if p.retry_kept else "not kept"
            retry = f"retried on {p.retry_trigger} (best {retry_best}, {kept})"
        handoff = f"HANDOFF ({p.handoff_reason})" if p.handoff_reason else "answered"
        ticket = f", ticket {p.ticket_id}" if p.ticket_id else ""
        lines.append(f"flow       : best score {best}, grade {p.grade}, {retry}, {handoff}{ticket}")
    return lines


def describe_timings(answer: Answer) -> str:
    """The step timings in ms, one line."""
    t = answer.timings_ms
    steps = [
        s
        for s in ("rewrite", "retrieve", "rerank", "retry_retrieve", "retry_rerank", "generate")
        if s in t
    ]
    return " | ".join(f"{s} {t[s]:.0f} ms" for s in steps)

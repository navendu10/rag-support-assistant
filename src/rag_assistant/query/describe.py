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


def describe_timings(answer: Answer) -> str:
    """The step timings in ms, one line."""
    t = answer.timings_ms
    steps = [s for s in ("rewrite", "retrieve", "rerank", "generate") if s in t]
    return " | ".join(f"{s} {t[s]:.0f} ms" for s in steps)

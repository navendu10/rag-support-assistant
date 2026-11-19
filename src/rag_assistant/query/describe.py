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
        if p.guard_triggered:
            again = "regenerated once" if p.guard_regenerated else "not regenerated"
            lines.append(f"guard      : rejected ({again}, {p.generations} generations)")
            lines += [f"             - {problem}" for problem in p.guard_problems]
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


def _summary(result: dict) -> str:
    """One short line for a tool result: its status and its first facts."""
    status = str(result.get("status", "?"))
    if "orders" in result:
        ids = ", ".join(o["order_id"] for o in result["orders"]) or "none"
        return f"{status}: {len(result['orders'])} orders ({ids})"
    if "passages" in result:
        docs = ", ".join(f"{p['id']} {p['doc_id']}" for p in result["passages"]) or "no passages"
        return f"{status}: {docs}"
    facts = result.get("facts") or [result.get("message", "")]
    return f"{status}: {'; '.join(str(f) for f in facts[:2])}"[:160]


def describe_agent(answer: Answer) -> list[str]:
    """v8: each tool call with its arguments and a one-line result, then the route."""
    agent = answer.agent
    if agent is None:
        return []
    lines = [
        f"tool {n}    : {c.name}({', '.join(f'{k}={v!r}' for k, v in c.args.items())})"
        f" -> {_summary(c.result)}"
        for n, c in enumerate(agent.calls, start=1)
    ]
    limit = ", HIT THE STEP LIMIT" if agent.hit_step_limit else ""
    lines.append(
        f"agent      : {agent.tool_calls} tool calls, {agent.model_calls} model calls, "
        f"{agent.generations} answer generations, {agent.outcome}{limit}"
    )
    lines += [f"guard      : {p}" for p in agent.guard_problems]
    return lines


def describe_draft(draft: dict) -> list[str]:
    """v9: a draft waiting for approval, as a box (the fields come from our code, not the
    model's description of them)."""
    rows = [
        ("draft", draft["draft_id"]),
        ("kind", draft["kind"]),
        ("order", draft.get("order_id") or "(no order)"),
        ("customer", draft["customer_id"]),
        ("reason", draft["reason"]),
        ("hash", draft["hash"][:12]),
    ]
    width = max(len(f"{k:<9}: {v}") for k, v in rows) + 2
    title = " DRAFT TICKET - WAITING FOR YOUR APPROVAL "
    width = max(width, len(title) + 2)
    lines = [f"+{title.center(width, '-')}+"]
    lines += [f"| {f'{k:<9}: {v}':<{width - 2}} |" for k, v in rows]
    lines.append(f"+{'-' * width}+")
    return lines

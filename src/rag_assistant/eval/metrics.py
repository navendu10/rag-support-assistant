"""Pure, network-free metric functions and result aggregation.

Every definition here is documented in docs/evaluation.md. String checks are
case-insensitive substring matches on normalised text (see ``normalise_for_match``).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from statistics import quantiles

from pydantic import BaseModel, ConfigDict

from rag_assistant.schemas import GoldenItem, User

INTERNAL = "internal"
_VERDICT = re.compile(r"VERDICT[\s*_`]*[:=][\s*_`\"']*([A-Za-z]+)", re.IGNORECASE)
_REASON = re.compile(r"REASON[\s*_`]*[:=][\s*_`]*(.+)", re.IGNORECASE)

# Folded before matching, because LLMs restyle text: "£ 20" for "GBP 20",
# "**lifetime warranty**", non-breaking hyphens and narrow no-break spaces.
_DASHES = dict.fromkeys(map(ord, "‐‑‒–—−"), "-")
_SPACES = dict.fromkeys(map(ord, "    "), " ")
_CURRENCY = {"₹": " rs ", "£": " gbp ", "$": " usd ", "€": " eur "}
_EMPHASIS = re.compile(r"[*_`]+")
_CURRENCY_WORDS = ((re.compile(r"\binr\b"), "rs"), (re.compile(r"\brs\."), "rs"))
_WHITESPACE = re.compile(r"\s+")


class Judgement(BaseModel):
    """A judge verdict; ``verdict`` is None when the judge output was unparseable."""

    model_config = ConfigDict(frozen=True)

    verdict: str | None
    reason: str


class RetrievedRef(BaseModel):
    """What the evaluator records about one retrieved chunk."""

    model_config = ConfigDict(frozen=True)

    doc_id: str
    chunk_index: int
    audience: str
    region: str
    score: float
    section: str | None = None
    page: int | None = None


class ItemChecks(BaseModel):
    """String- and metadata-based checks for one answered question."""

    model_config = ConfigDict(frozen=True)

    recall_hit: bool | None
    retrieval_leak: bool | None
    answer_leak: bool | None
    injection_fail: bool | None
    forbidden_hits: list[str]


class ResultRow(BaseModel):
    """One line of results.jsonl."""

    model_config = ConfigDict(frozen=True)

    id: str
    question: str
    user_id: str
    user_role: str
    user_region: str
    failure_tag: str
    topic: str
    history_turns: int
    expected_doc_ids: list[str]
    must_not_contain: list[str]
    expect_handoff: bool
    retrieved: list[RetrievedRef]
    context_doc_ids: list[str]
    context_audiences: list[str]
    answer: str
    checks: ItemChecks
    correctness: Judgement
    handoff: Judgement | None
    latency_ms: dict[str, float]


# ---------------------------------------------------------------- per item


def recall_hit(expected_doc_ids: Sequence[str], retrieved_doc_ids: Sequence[str]) -> bool | None:
    """True if any expected doc is retrieved; None (n/a) if nothing is expected."""
    if not expected_doc_ids:
        return None
    return bool(set(expected_doc_ids) & set(retrieved_doc_ids))


def normalise_for_match(text: str) -> str:
    """Lower-case and fold styling so equivalent phrasings compare equal.

    Currency symbols become codes (₹/£/$ -> rs/gbp/usd), Markdown emphasis is
    removed, Unicode dashes and spaces are folded, and whitespace is collapsed.
    """
    folded = _EMPHASIS.sub("", text.translate(_DASHES).translate(_SPACES))
    for symbol, word in _CURRENCY.items():
        folded = folded.replace(symbol, word)
    folded = folded.lower()
    for pattern, replacement in _CURRENCY_WORDS:
        folded = pattern.sub(replacement, folded)
    return _WHITESPACE.sub(" ", folded).strip()


def find_forbidden(answer: str, phrases: Iterable[str]) -> list[str]:
    """Phrases from ``phrases`` that occur in ``answer`` after normalisation."""
    normalised = normalise_for_match(answer)
    return [p for p in phrases if normalise_for_match(p) in normalised]


def retrieval_leak(user: User, context_audiences: Sequence[str]) -> bool | None:
    """For customers: did any context chunk come from an internal document? None for agents."""
    if user.role != "customer":
        return None
    return INTERNAL in context_audiences


def check_item(
    item: GoldenItem,
    user: User,
    retrieved_doc_ids: Sequence[str],
    context_audiences: Sequence[str],
    answer: str,
) -> ItemChecks:
    """Run every non-LLM check for one question."""
    hits = find_forbidden(answer, item.must_not_contain)
    is_customer = user.role == "customer"
    return ItemChecks(
        recall_hit=recall_hit(item.expected_doc_ids, retrieved_doc_ids),
        retrieval_leak=retrieval_leak(user, context_audiences),
        answer_leak=bool(hits) if item.failure_tag == "leak" and is_customer else None,
        injection_fail=bool(hits) if item.failure_tag == "injection" else None,
        forbidden_hits=hits,
    )


def parse_verdict(text: str, allowed: Sequence[str]) -> Judgement:
    """Parse ``VERDICT: X`` / ``REASON: ...`` judge output.

    The verdict is None if it is missing or not one of ``allowed``; the raw text is
    then kept as the reason so it can be inspected.
    """
    verdict_match = _VERDICT.search(text)
    reason_match = _REASON.search(text)
    reason = reason_match.group(1).strip() if reason_match else text.strip()[:300]
    verdict = verdict_match.group(1).upper() if verdict_match else None
    if verdict not in {a.upper() for a in allowed}:
        return Judgement(verdict=None, reason=f"unparseable judge output: {text.strip()[:300]}")
    return Judgement(verdict=verdict, reason=reason)


# ------------------------------------------------------------- aggregation


def rate(flags: Iterable[bool | None]) -> dict[str, int]:
    """Count True among non-None flags: ``{"count": k, "total": n}``."""
    values = [f for f in flags if f is not None]
    return {"count": sum(values), "total": len(values)}


def format_rate(r: dict[str, int]) -> str:
    """Render a rate as ``k/n (p%)`` or ``n/a``."""
    if r["total"] == 0:
        return "n/a"
    return f"{r['count']}/{r['total']} ({100 * r['count'] / r['total']:.0f}%)"


def _correct(row: ResultRow) -> bool:
    """True if the correctness judge said CORRECT."""
    return row.correctness.verdict == "CORRECT"


def _handed_off(row: ResultRow) -> bool | None:
    """True/False for expect_handoff questions, None otherwise."""
    if not row.expect_handoff:
        return None
    return row.handoff is not None and row.handoff.verdict == "HANDOFF"


def _forbidden(row: ResultRow) -> bool | None:
    """Whether the answer hit a forbidden phrase; None if the item has none."""
    return bool(row.checks.forbidden_hits) if row.must_not_contain else None


def _percentile(values: Sequence[float], pct: int) -> float:
    """Simple percentile (inclusive method); 0 for empty input."""
    if not values:
        return 0.0
    if len(values) == 1:
        return float(values[0])
    return round(quantiles(values, n=100, method="inclusive")[pct - 1], 1)


def summarise_rows(rows: Sequence[ResultRow]) -> dict[str, object]:
    """Headline metrics for a set of rows."""
    totals = [row.latency_ms.get("total", 0.0) for row in rows]
    return {
        "questions": len(rows),
        "recall_at_k": rate(row.checks.recall_hit for row in rows),
        "correctness": rate(_correct(row) for row in rows),
        "retrieval_leaks": rate(row.checks.retrieval_leak for row in rows),
        "answer_leaks": rate(row.checks.answer_leak for row in rows),
        "injections": rate(row.checks.injection_fail for row in rows),
        "handoffs": rate(_handed_off(row) for row in rows),
        "forbidden_phrases": rate(_forbidden(row) for row in rows),
        "judge_errors": sum(
            1
            for row in rows
            for j in (row.correctness, row.handoff)
            if j is not None and j.verdict is None
        ),
        "latency_ms": {"p50": _percentile(totals, 50), "p95": _percentile(totals, 95)},
    }


def summarise_by_tag(rows: Sequence[ResultRow]) -> dict[str, dict[str, object]]:
    """``summarise_rows`` for each failure tag, in first-seen order."""
    tags = list(dict.fromkeys(row.failure_tag for row in rows))
    return {tag: summarise_rows([r for r in rows if r.failure_tag == tag]) for tag in tags}

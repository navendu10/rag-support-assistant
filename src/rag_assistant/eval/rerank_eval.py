"""Retrieval-only analysis for reranking (no LLM calls).

Used by ``scripts/compare_rerankers.py`` (which cross-encoder to use) and
``scripts/rerank_score_distribution.py`` (what a ``min_score`` threshold would do).
Scores here are the sigmoid-normalised 0-1 rerank scores.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from statistics import median

from rag_assistant.eval.metrics import first_expected_rank
from rag_assistant.retrieval.vector import RetrievedChunk
from rag_assistant.schemas import GoldenItem


def latency_stats(latencies_ms: Sequence[float]) -> dict[str, float]:
    """Mean and p95 (nearest rank) of per-question latencies, in ms."""
    if not latencies_ms:
        return {"mean": 0.0, "p95": 0.0}
    ordered = sorted(latencies_ms)
    p95 = ordered[min(len(ordered) - 1, max(0, round(0.95 * len(ordered)) - 1))]
    return {"mean": round(sum(ordered) / len(ordered), 1), "p95": round(p95, 1)}


def rank_cell(item: GoldenItem, doc_ids: Sequence[str]) -> str:
    """Rank of the first expected chunk in ``doc_ids`` (``-`` if absent)."""
    rank = first_expected_rank(item.expected_doc_ids, doc_ids)
    return "-" if rank is None else str(rank)


def rank_table(items: Sequence[GoldenItem], runs: Mapping[str, Mapping[str, Sequence[str]]]) -> str:
    """Markdown: per question, the rank of its first expected chunk in each run.

    ``runs`` maps a run label to {question id: ranked doc_ids (all fetch_k)}. Only
    questions whose rank differs between runs are listed.
    """
    labels = list(runs)
    lines = [
        "| id | tag | question | " + " | ".join(labels) + " |",
        "|---|---|---|" + "---|" * len(labels),
    ]
    for item in items:
        cells = [rank_cell(item, runs[label][item.id]) for label in labels]
        if len(set(cells)) > 1:
            question = item.question if len(item.question) <= 60 else item.question[:57] + "..."
            lines.append(
                f"| {item.id} | {item.failure_tag} | {question} | " + " | ".join(cells) + " |"
            )
    return "\n".join(lines)


@dataclass(frozen=True)
class BestScores:
    """The best rerank score of a chunk from an expected doc and of any other chunk."""

    item_id: str
    tag: str
    best_expected: float | None
    best_other: float | None
    best_other_doc: str | None


def best_scores(item: GoldenItem, reranked: Sequence[RetrievedChunk]) -> BestScores:
    """Best 0-1 score among expected-doc chunks and among all other chunks."""
    expected = [c for c in reranked if c.doc_id in item.expected_doc_ids]
    other = [c for c in reranked if c.doc_id not in item.expected_doc_ids]
    top_other = max(other, key=lambda c: c.rerank_score or 0.0, default=None)
    return BestScores(
        item_id=item.id,
        tag=item.failure_tag,
        best_expected=max((c.rerank_score or 0.0 for c in expected), default=None),
        best_other=None if top_other is None else top_other.rerank_score,
        best_other_doc=None if top_other is None else top_other.doc_id,
    )


def summary_row(label: str, values: Sequence[float]) -> str:
    """``| label | n | min | median | max |`` for a list of scores."""
    if not values:
        return f"| {label} | 0 | - | - | - |"
    return (
        f"| {label} | {len(values)} | {min(values):.3f} | {median(values):.3f} | "
        f"{max(values):.3f} |"
    )


def threshold_table(
    answerable: Sequence[BestScores],
    unanswerable: Sequence[BestScores],
    thresholds: Sequence[float],
) -> str:
    """For each threshold: answerable questions that keep a relevant chunk, and
    unanswerable questions whose every chunk would be rejected."""
    lines = [
        "| min_score | answerable: relevant chunk kept | unanswerable: all chunks rejected |",
        "|---|---|---|",
    ]
    for t in thresholds:
        kept = sum((b.best_expected or 0.0) >= t for b in answerable)
        rejected = sum((b.best_other or 0.0) < t for b in unanswerable)
        lines.append(f"| {t:g} | {kept}/{len(answerable)} | {rejected}/{len(unanswerable)} |")
    return "\n".join(lines)


def suggest_min_score(
    answerable: Sequence[BestScores],
    unanswerable: Sequence[BestScores],
    thresholds: Sequence[float],
) -> float:
    """The threshold that rejects the most unanswerable questions while keeping every
    answerable question's relevant chunk; if none keeps them all, the one that keeps most.

    Ties go to the lower (more permissive) threshold.
    """

    def kept(t: float) -> int:
        return sum((b.best_expected or 0.0) >= t for b in answerable)

    def rejected(t: float) -> int:
        return sum((b.best_other or 0.0) < t for b in unanswerable)

    best_kept = max(kept(t) for t in thresholds)
    candidates = [t for t in sorted(thresholds) if kept(t) == best_kept]
    return max(candidates, key=lambda t: (rejected(t), -t))


# ------------------------------------------------------------- rerank sweep


@dataclass(frozen=True)
class Regressions:
    """Per-question changes of one run against a baseline run (question ids)."""

    lost_recall: tuple[str, ...]
    gained_recall: tuple[str, ...]
    lost_hit1: tuple[str, ...]
    gained_hit1: tuple[str, ...]


def _in_top(item: GoldenItem, doc_ids: Sequence[str], k: int) -> bool:
    """Whether an expected doc is in the first ``k`` of ``doc_ids``."""
    return bool(set(item.expected_doc_ids) & set(doc_ids[:k]))


def regressions(
    items: Sequence[GoldenItem],
    baseline: Mapping[str, Sequence[str]],
    run: Mapping[str, Sequence[str]],
    recall_k: int = 5,
) -> Regressions:
    """Questions whose recall@k or hit@1 changed between ``baseline`` and ``run``."""
    scored = [i for i in items if i.expected_doc_ids]

    def changed(k: int, was: bool) -> tuple[str, ...]:
        return tuple(
            i.id
            for i in scored
            if _in_top(i, baseline[i.id], k) == was and _in_top(i, run[i.id], k) != was
        )

    return Regressions(
        lost_recall=changed(recall_k, True),
        gained_recall=changed(recall_k, False),
        lost_hit1=changed(1, True),
        gained_hit1=changed(1, False),
    )


def contaminated(
    question_codes: Sequence[str], context_doc_ids: Sequence[str], doc_codes: Mapping[str, str]
) -> bool:
    """A question naming a product whose context holds a chunk of a different product.

    Only *product* codes count (those in ``doc_codes``' values): a question that names
    just an error code, such as "WashPro is showing E-74", names no product, so the
    WM-700 manual in its context is not contamination.
    """
    products = set(question_codes) & set(doc_codes.values())
    if not products:
        return False
    return any(doc in doc_codes and doc_codes[doc] not in products for doc in context_doc_ids)


@dataclass(frozen=True)
class SweepResult:
    """Headline numbers of one sweep setting, for choosing a configuration."""

    label: str
    recall: int
    hit1: int
    mrr: float
    lost_recall: int
    regressions: int
    contaminated: int
    latency_ms: float


def pick_setting(results: Sequence[SweepResult], min_recall: int) -> SweepResult | None:
    """Best setting that loses no question's recall@5 and keeps recall >= ``min_recall``.

    "Doesn't lose recall@5 against the baseline" is checked per question, not only as a
    count: a setting that drops one question's answer out of the top 5 while gaining
    two others is not eligible. Eligible settings are ranked by hit@1, then MRR, then
    fewest per-question regressions (recall or hit@1), then fewest contaminated code
    questions, then lowest latency. None if no setting qualifies.
    """
    eligible = [r for r in results if r.recall >= min_recall and r.lost_recall == 0]
    if not eligible:
        return None
    return min(
        eligible,
        key=lambda r: (-r.hit1, -r.mrr, r.regressions, r.contaminated, r.latency_ms),
    )

"""Retrieval-only alpha sweep for hybrid search (no LLM calls).

``scripts/sweep_alpha.py`` runs every golden question at each (fusion, alpha)
setting and records the top-5 doc_ids. The functions here score those runs per
question group and render the Markdown tables saved to ``results/<stage>/alpha_sweep.md``.

Besides recall@5, hit@1 and MRR, each cell reports a sweep-only diagnostic, **ctx**:
how many of the stage's ``top_k`` context chunks (what the LLM actually reads) come
from an expected doc, summed over the group. Ranking metrics only look at the first
expected chunk; ctx also shows a competing product's chunk crowding the context.

Dynamic alpha uses one alpha for questions with a detected code and another for the
rest, so its results are assembled from the fixed-alpha runs without new queries.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from rag_assistant.eval.metrics import (
    format_mean,
    hit_at_1,
    mean_value,
    rate,
    recall_hit,
    reciprocal_rank,
)
from rag_assistant.retrieval.codes import CodeDetector
from rag_assistant.schemas import GoldenItem

GROUPS = ("all", "product_code", "code detected", "no code")
Retrieved = Mapping[str, Sequence[str]]  # question id -> top-5 doc_ids


def question_groups(
    items: Sequence[GoldenItem], detector: CodeDetector
) -> dict[str, list[GoldenItem]]:
    """Items with expected docs, split into the groups in ``GROUPS``."""
    scored = [i for i in items if i.expected_doc_ids]
    coded = [i for i in scored if detector.find(i.question)]
    return {
        "all": scored,
        "product_code": [i for i in scored if i.failure_tag == "product_code"],
        "code detected": coded,
        "no code": [i for i in scored if i not in coded],
    }


def context_hits(items: Sequence[GoldenItem], retrieved: Retrieved, top_k: int) -> dict[str, int]:
    """Chunks from an expected doc among each item's first ``top_k``, summed over ``items``."""
    return {
        "count": sum(
            sum(doc in i.expected_doc_ids for doc in retrieved[i.id][:top_k]) for i in items
        ),
        "total": sum(len(retrieved[i.id][:top_k]) for i in items),
    }


def score_group(
    items: Sequence[GoldenItem], retrieved: Retrieved, top_k: int = 4
) -> dict[str, dict]:
    """Recall@5, hit@1, MRR and context hits (first ``top_k``) of one run over ``items``."""
    return {
        "context": context_hits(items, retrieved, top_k),
        "recall_at_k": rate(recall_hit(i.expected_doc_ids, retrieved[i.id]) for i in items),
        "hit_at_1": rate(hit_at_1(i.expected_doc_ids, retrieved[i.id]) for i in items),
        "mrr": mean_value(reciprocal_rank(i.expected_doc_ids, retrieved[i.id]) for i in items),
    }


def dynamic_run(
    code_run: Retrieved, default_run: Retrieved, items: Sequence[GoldenItem], detector: CodeDetector
) -> dict[str, Sequence[str]]:
    """Per question: the code_alpha run if it contains a code, else the default_alpha run."""
    return {i.id: (code_run if detector.find(i.question) else default_run)[i.id] for i in items}


def _counts(r: dict[str, int]) -> str:
    """``k/n`` for a rate."""
    return f"{r['count']}/{r['total']}"


def metrics_cell(scores: dict[str, dict]) -> str:
    """One table cell: ``recall@5 · hit@1 · MRR · ctx``."""
    return " · ".join(
        [
            _counts(scores["recall_at_k"]),
            _counts(scores["hit_at_1"]),
            format_mean(scores["mrr"]),
            _counts(scores["context"]),
        ]
    )


def sweep_table(
    rows: Sequence[tuple[str, Retrieved]],
    groups: Mapping[str, Sequence[GoldenItem]],
    top_k: int = 4,
) -> str:
    """Markdown table: one row per setting, one ``recall@5 · hit@1 · MRR · ctx`` cell per group."""
    header = "| Setting | " + " | ".join(f"{g} (n={len(groups[g])})" for g in GROUPS) + " |"
    lines = [header, "|---|" + "---|" * len(GROUPS)]
    for label, retrieved in rows:
        cells = [metrics_cell(score_group(groups[g], retrieved, top_k)) for g in GROUPS]
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def dynamic_matrix(
    runs: Mapping[float, Retrieved],
    items: Sequence[GoldenItem],
    detector: CodeDetector,
    top_k: int = 4,
) -> str:
    """All-question ``recall@5 · hit@1 · MRR · ctx`` for every (code_alpha, default_alpha)."""
    alphas = sorted(runs)
    lines = [
        "| code_alpha \\ default_alpha | " + " | ".join(f"{a:g}" for a in alphas) + " |",
        "|---|" + "---|" * len(alphas),
    ]
    for code_alpha in alphas:
        cells = []
        for default_alpha in alphas:
            run = dynamic_run(runs[code_alpha], runs[default_alpha], items, detector)
            cells.append(metrics_cell(score_group(items, run, top_k)))
        lines.append(f"| {code_alpha:g} | " + " | ".join(cells) + " |")
    return "\n".join(lines)

"""Rerank score distribution and a suggested min_score (retrieval only, no LLM calls).

Usage:
    python scripts/rerank_score_distribution.py            # stage v3
    python scripts/rerank_score_distribution.py --stage v3

For each golden question, the sigmoid-normalised 0-1 rerank score of the best chunk
from an expected doc against the best chunk from any other doc, plus the best score
for questions with no relevant public doc. Saves results/<stage>/score_distribution.md
with a threshold table and a suggested min_score. The suggestion is NOT applied: the
stage keeps min_score: null until Session 8.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date

from rag_assistant.config import get_settings, load_stage
from rag_assistant.embeddings import Embedder
from rag_assistant.eval.rerank_eval import (
    BestScores,
    best_scores,
    suggest_min_score,
    summary_row,
    threshold_table,
)
from rag_assistant.ingestion.store import connect
from rag_assistant.retrieval.factory import make_retriever
from rag_assistant.retrieval.rerank import Reranker
from rag_assistant.schemas import load_golden, load_users

THRESHOLDS = (0.01, 0.02, 0.05, 0.1, 0.2, 0.3, 0.5, 0.7, 0.9)


def parse_args() -> argparse.Namespace:
    """Parse command-line options."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stage", default="v3", help="a stage with rerank: true")
    return parser.parse_args()


def _fmt(score: float | None) -> str:
    """A score cell."""
    return "-" if score is None else f"{score:.3f}"


def main() -> int:
    """Score every question's candidates and save the distribution."""
    args = parse_args()
    stage, settings = load_stage(args.stage), get_settings()
    if not stage.rerank:
        print(f"Stage {stage.name} has rerank: false")
        return 1
    items = load_golden(settings.paths.resolve(settings.paths.golden_file))
    roles = {
        u.user_id: u.role for u in load_users(settings.paths.resolve(settings.paths.users_file))
    }
    reranker = Reranker(str(stage.rerank_model))
    reranker.warm_up()
    with connect(settings.weaviate) as client:
        retriever = make_retriever(
            stage,
            client.collections.get(stage.collection_name),
            Embedder(settings.embedding),
            settings.paths.resolve(settings.paths.generated_dir) / "manifest.json",
        )
        scores = {
            i.id: best_scores(
                i, reranker.rerank(i.question, retriever.search(i.question, int(stage.fetch_k)))
            )
            for i in items
        }

    answerable = [scores[i.id] for i in items if i.expected_doc_ids]
    leaks = [
        scores[i.id]
        for i in items
        if not i.expected_doc_ids and i.failure_tag == "leak" and roles[i.user_id] == "customer"
    ]
    unanswerable = [
        scores[i.id] for i in items if not i.expected_doc_ids and scores[i.id] not in leaks
    ]
    suggested = suggest_min_score(answerable, unanswerable, THRESHOLDS)

    def question_rows(group: list[BestScores]) -> str:
        """Per-question rows for one group."""
        lines = [
            "| id | tag | best expected-doc chunk | best other chunk | other doc |",
            "|---|---|---|---|---|",
        ]
        lines += [
            f"| {b.item_id} | {b.tag} | {_fmt(b.best_expected)} | {_fmt(b.best_other)} | "
            f"{b.best_other_doc or '-'} |"
            for b in group
        ]
        return "\n".join(lines)

    summary = "\n".join(
        [
            "| Group | n | min | median | max |",
            "|---|---|---|---|---|",
            summary_row(
                "answerable: best expected-doc chunk",
                [b.best_expected for b in answerable if b.best_expected is not None],
            ),
            summary_row(
                "answerable: best other-doc chunk",
                [b.best_other for b in answerable if b.best_other is not None],
            ),
            summary_row(
                "no relevant doc: best chunk",
                [b.best_other for b in unanswerable if b.best_other is not None],
            ),
            summary_row(
                "customer leak items: best chunk",
                [b.best_other for b in leaks if b.best_other is not None],
            ),
        ]
    )
    sections = [
        f"# Rerank score distribution: stage `{stage.name}`",
        "\n".join(
            [
                f"- Date: {date.today().isoformat()}; retrieval only, no LLM calls",
                f"- Model `{stage.rerank_model}`; {stage.fetch_k} candidates per question; "
                "scores are sigmoid(logit), 0-1",
                "- 'No relevant doc' = items with no expected document (hallucination and "
                "injection-control items). Customer **leak** items are listed separately: "
                "their relevant document is an internal playbook, which a reranker should "
                "score highly",
                f"- **Suggested min_score: {suggested:g}** (from the thresholds below). "
                f"NOT applied: `{stage.name}` keeps `min_score: null` until Session 8",
                "- Tuned on the same golden set used for scoring, so it is optimistic",
            ]
        ),
        "## Summary",
        summary,
        "## Thresholds",
        threshold_table(answerable, unanswerable, THRESHOLDS),
        "## Answerable questions",
        question_rows(answerable),
        "## No relevant doc",
        question_rows(unanswerable),
        "## Customer leak items",
        question_rows(leaks),
    ]
    text = "\n\n".join(sections) + "\n"
    sys.stdout.reconfigure(encoding="utf-8")
    print(text)
    out = settings.paths.resolve(settings.paths.results_dir) / stage.name
    out.mkdir(parents=True, exist_ok=True)
    (out / "score_distribution.md").write_text(text, encoding="utf-8", newline="\n")
    print(f"Saved {out / 'score_distribution.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

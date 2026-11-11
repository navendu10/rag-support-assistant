"""Retrieval-only check of query rewriting: no answers, no judges (Session 6).

Usage:
    python scripts/check_rewrite_retrieval.py                      # v3, v4a, v4
    python scripts/check_rewrite_retrieval.py --stages v4a,v4

For each stage it rewrites every golden question (small model, cached; v3 doesn't
rewrite), searches, reranks and scores the top 5 the same way evaluate.py does. It then
compares the result with the stage's stored full run, so a change to the rewriter can be
judged before spending a full evaluation. The table is printed and saved to
results/<last stage>/retrieval_check.md.
"""

from __future__ import annotations

import argparse
import sys

from rag_assistant.config import get_settings, load_stage
from rag_assistant.eval.rewrites import rank_changes, score_retrieval
from rag_assistant.eval.runner import load_rows, output_dir
from rag_assistant.pipeline import Pipeline
from rag_assistant.schemas import load_golden, load_users


def parse_args() -> argparse.Namespace:
    """Parse command-line options."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stages", default="v3,v4a,v4", help="comma-separated stage names")
    return parser.parse_args()


def _rank(rank: int | None) -> str:
    """A rank or ``-`` when absent from the top 5."""
    return "-" if rank is None else str(rank)


def main() -> int:
    """Retrieve for every stage, compare with the stored runs, print and save the table."""
    args = parse_args()
    settings = get_settings()
    k = settings.eval.recall_k
    items = load_golden(settings.paths.resolve(settings.paths.golden_file))
    users = {u.user_id: u for u in load_users(settings.paths.resolve(settings.paths.users_file))}
    expected = {i.id: i.expected_doc_ids for i in items}
    lines = [
        f"# Retrieval-only check: {args.stages}",
        "",
        "Written by `python scripts/check_rewrite_retrieval.py`. Recall@5 · hit@1 · MRR over the "
        f"{sum(1 for d in expected.values() if d)} golden questions with an expected document. "
        "*Stored* is the committed full run; *now* is the current code, retrieval only.",
        "",
        "| Stage | Stored | Now |",
        "|---|---|---|",
    ]
    details: list[str] = []
    for name in args.stages.split(","):
        stage = load_stage(name.strip())
        out_dir = output_dir(settings, stage, filtered=False)
        stored = {r.id: [x.doc_id for x in r.retrieved[:k]] for r in load_rows(out_dir)}
        now: dict[str, list[str]] = {}
        with Pipeline.from_stage(stage, settings) as pipeline:
            for item in items:
                search = standalone = item.question
                codes: tuple[str, ...] = ()
                if stage.query_rewrite:
                    result, _ = pipeline.rewrite(item.question, item.history)
                    search = result.rewrite.search_query
                    standalone = result.rewrite.standalone_question
                    codes = tuple(result.rewrite.product_codes)
                ranked = pipeline.retrieve_ranked(
                    search,
                    k,
                    rerank_query=standalone,
                    extra_codes=codes,
                    regions=pipeline.regions_for(users[item.user_id]),
                    user=users[item.user_id],
                ).ranked
                now[item.id] = [c.doc_id for c in ranked[:k]]
                print(f"{stage.name} {item.id} {now[item.id]}", flush=True)
        before, after = score_retrieval(expected, stored), score_retrieval(expected, now)
        lines.append(f"| {stage.name} | {before.cell()} | {after.cell()} |")
        changes = rank_changes(expected, stored, now)
        details += [f"\n## {stage.name}: first expected rank, stored -> now\n"]
        details += [f"- {i}: {_rank(a)} -> {_rank(b)}" for i, a, b in changes] or ["- no change"]
    text = "\n".join(lines + details) + "\n"
    print("\n" + text)
    path = output_dir(settings, load_stage(args.stages.split(",")[-1].strip()), filtered=False)
    (path / "retrieval_check.md").write_text(text, encoding="utf-8")
    print(f"Saved {path / 'retrieval_check.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

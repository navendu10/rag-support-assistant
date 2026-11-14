"""Isolate the effect of a rewrite change on retrieval (no LLM calls).

Usage:
    python scripts/compare_rewrite_retrieval.py --stage v7 --rewrites-from v6

Session 9 added six products to the rewriter's catalogue, so v7's rewrites can differ from
v6's. This script searches **the same index** (the stage's) twice per golden question:
once with the other stage's stored rewrite and once with the stage's own, and reports how
many rewrites changed and recall@5, hit@1 and MRR for each. Only the first search is
compared (no corrective-flow retry), so the difference is the rewrites alone. Writes
results/<stage>/rewrite_retrieval.md.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date

from rag_assistant.config import get_settings, load_stage
from rag_assistant.eval.metrics import (
    RewriteRecord,
    format_mean,
    format_rate,
    hit_at_1,
    mean_value,
    rate,
    recall_hit,
    reciprocal_rank,
)
from rag_assistant.eval.runner import load_rows, output_dir
from rag_assistant.graph.handoff import MemoryTicketStore
from rag_assistant.pipeline import Pipeline
from rag_assistant.schemas import load_golden, load_users


def parse_args() -> argparse.Namespace:
    """Parse command-line options."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stage", default="v7", help="the index to search, and its rewrites")
    parser.add_argument("--rewrites-from", default="v6", help="the stage whose rewrites to compare")
    return parser.parse_args()


def _same(a: RewriteRecord, b: RewriteRecord) -> bool:
    """Same search query, standalone question and product codes."""
    return (a.search_query, a.standalone_question, a.product_codes) == (
        b.search_query,
        b.standalone_question,
        b.product_codes,
    )


def _cell(text: str) -> str:
    """Make text safe for a Markdown table cell."""
    return text.replace("|", "\\|").replace("\n", " ")


def main() -> int:
    """Search the stage's index with both stages' rewrites and report the difference."""
    args = parse_args()
    settings = get_settings()
    stage, other = load_stage(args.stage), load_stage(args.rewrites_from)
    items = {i.id: i for i in load_golden(settings.paths.resolve(settings.paths.golden_file))}
    users = {u.user_id: u for u in load_users(settings.paths.resolve(settings.paths.users_file))}
    own = {r.id: r for r in load_rows(output_dir(settings, stage, False))}
    theirs = {r.id: r for r in load_rows(output_dir(settings, other, False))}
    k = settings.eval.recall_k
    scores: dict[str, dict[str, list]] = {s: {"hit": [], "h1": [], "rr": []} for s in ("a", "b")}
    changed = []
    with Pipeline.from_stage(stage, settings, tickets=MemoryTicketStore()) as pipeline:
        for item_id, row in own.items():
            a, b = theirs[item_id].rewrite, row.rewrite
            if a is None or b is None:
                continue
            item, user = items[item_id], users[row.user_id]
            for key, rw in (("a", a), ("b", b)):
                found = pipeline.retrieve_ranked(
                    rw.search_query,
                    k,
                    rerank_query=rw.standalone_question,
                    extra_codes=rw.product_codes,
                    regions=pipeline.regions_for(user),
                    user=user,
                )
                ids = [c.doc_id for c in found.ranked[:k]]
                scores[key]["hit"].append(recall_hit(item.expected_doc_ids, ids))
                scores[key]["h1"].append(hit_at_1(item.expected_doc_ids, ids))
                scores[key]["rr"].append(reciprocal_rank(item.expected_doc_ids, ids))
            if not _same(a, b):
                changed.append((item_id, a, b))
    lines = [
        f"# Rewrite change and retrieval: stage `{stage.name}`",
        f"- Date: {date.today().isoformat()}; the `{stage.name}` index searched with "
        f"`{other.name}`'s stored rewrites and with `{stage.name}`'s; first search only, no LLM "
        "calls",
        f"- Rewrites changed: **{len(changed)}/{len(own)}** (search query, standalone question "
        "or product codes)",
        "",
        f"| Rewrites | Recall@{k} | Hit@1 | MRR |",
        "|---|---|---|---|",
    ]
    for key, label in (("a", other.name), ("b", stage.name)):
        s = scores[key]
        lines.append(
            f"| {label}'s | {format_rate(rate(s['hit']))} | {format_rate(rate(s['h1']))} | "
            f"{format_mean(mean_value(s['rr']))} |"
        )
    lines += [
        "",
        f"| id | {other.name} search query | {stage.name} search query | {stage.name} codes |",
        "|---|---|---|---|",
    ]
    lines += [
        f"| {i} | {_cell(a.search_query)} | {_cell(b.search_query)} | "
        f"{', '.join(b.product_codes) or '-'} |"
        for i, a, b in changed
    ]
    out = output_dir(settings, stage, False) / "rewrite_retrieval.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print("\n".join(lines[:9]))
    print(f"\nWrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

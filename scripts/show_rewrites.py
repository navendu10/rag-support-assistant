"""Show every query rewrite for a stage: stored from a full run, or live (no answers).

Usage:
    python scripts/show_rewrites.py --stage v4          # from results/v4/results.jsonl
    python scripts/show_rewrites.py --stage v4 --live   # rewrite all golden questions now

The stored mode makes no LLM calls. ``--live`` makes one small-model call per question
(cached afterwards) and nothing else: no search, no answers, no judges. That is the cheap
way to read new rewrites before spending a full evaluation. Each mode prints a Markdown
table (question, short history, standalone_question, search_query, category, codes,
flags) and saves it: results/<stage>/rewrites.md, or rewrites_live.md for --live.
"""

from __future__ import annotations

import argparse
import sys

from rag_assistant.config import get_settings, load_stage
from rag_assistant.eval.rewrites import RewriteChecker, rewrites_table, stored_entries
from rag_assistant.eval.runner import load_rows, output_dir
from rag_assistant.pipeline import make_rewriter
from rag_assistant.schemas import load_golden

HEADER = """# Query rewrites: stage `{stage}` ({source})

Written by `python scripts/show_rewrites.py --stage {stage}{flag}`. `standalone_question`
goes to the reranker and the answer; `search_query` goes to hybrid search. Flags:
FALLBACK (invalid output twice, raw message used), invented product, dropped product,
dropped code, added or dropped country, added condition (see docs/evaluation.md).

"""


def parse_args() -> argparse.Namespace:
    """Parse command-line options."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stage", required=True, help="a stage with query_rewrite, e.g. v4")
    parser.add_argument("--live", action="store_true", help="rewrite now instead of reading a run")
    return parser.parse_args()


def main() -> int:
    """Print the table and save it next to the stage's results."""
    args = parse_args()
    settings = get_settings()
    stage = load_stage(args.stage)
    if not stage.query_rewrite:
        print(f"Stage {stage.name} has query_rewrite: false.")
        return 1
    items = load_golden(settings.paths.resolve(settings.paths.golden_file))
    out_dir = output_dir(settings, stage, filtered=False)
    if args.live:
        rewriter, checker = make_rewriter(settings), RewriteChecker.from_settings(settings)
        turns = (lambda i: i.history) if stage.use_history else (lambda i: [])
        entries = [(i, checker.record(rewriter.rewrite(i.question, turns(i)), i)) for i in items]
        name, source = "rewrites_live.md", "live"
    else:
        if not (out_dir / "results.jsonl").is_file():
            print(f"No full run for {stage.name}. Run: scripts/evaluate.py --stage {stage.name}")
            return 1
        entries = stored_entries(load_rows(out_dir), items)
        name, source = "rewrites.md", "stored run"
    table = rewrites_table(entries)
    print(table)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / name
    flag = " --live" if args.live else ""
    path.write_text(
        HEADER.format(stage=stage.name, source=source, flag=flag) + table + "\n", encoding="utf-8"
    )
    print(f"\nSaved {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

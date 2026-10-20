"""Evaluate a stage on the golden set and write results/<stage>/.

Usage:
    python scripts/evaluate.py --stage v0 [--tag hallucination] [--limit 5]
    python scripts/evaluate.py --stage v0 --rescore

Filtered runs (--tag/--limit) are written to results/<stage>/subset/ (git-ignored).
--rescore re-applies the current metric code to the stored full run (no LLM calls).
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import get_args

from rag_assistant.config import get_settings, load_stage
from rag_assistant.eval.metrics import format_rate
from rag_assistant.eval.runner import rescore, run_evaluation
from rag_assistant.schemas import FailureTag


def parse_args() -> argparse.Namespace:
    """Parse command-line options."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stage", required=True, help="stage name, e.g. v0")
    parser.add_argument("--tag", choices=get_args(FailureTag), help="only this failure tag")
    parser.add_argument("--limit", type=int, help="only the first N questions")
    parser.add_argument(
        "--rescore", action="store_true", help="recompute checks on the stored full run"
    )
    return parser.parse_args()


def main() -> int:
    """Run the evaluation and print the headline numbers."""
    args = parse_args()
    start = time.perf_counter()
    stage, settings = load_stage(args.stage), get_settings()
    if args.rescore:
        out = rescore(stage, settings)
    else:
        out = run_evaluation(stage, settings, args.tag, args.limit)
    s = out.summary
    print(f"\nDone in {time.perf_counter() - start:.0f}s -> {out.out_dir}")
    for key in (
        "recall_at_k",
        "correctness",
        "retrieval_leaks",
        "answer_leaks",
        "injections",
        "handoffs",
    ):
        print(f"  {key:<16} {format_rate(s[key])}")
    if s["judge_errors"]:
        print(f"  judge_errors     {s['judge_errors']} (see results.jsonl)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

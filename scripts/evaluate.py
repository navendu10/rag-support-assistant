"""Evaluate a stage on the golden set and write results/<stage>/.

Usage:
    python scripts/evaluate.py --stage v0 [--tag hallucination] [--limit 5]
    python scripts/evaluate.py --stage v0 --rescore
    python scripts/evaluate.py --stage v0 --only-missing
    python scripts/evaluate.py --stage v0 --add-leak-judge
    python scripts/evaluate.py --stage v4 --add-handoff-judge
    python scripts/evaluate.py --stage v4 --add-invented-judge
    python scripts/evaluate.py --stage v6 --only-ids g011,g046

Filtered runs (--tag/--limit) are written to results/<stage>/subset/ (git-ignored).
--rescore re-applies the current metric code to the stored full run (no LLM calls).
--only-missing answers only golden questions the stored full run lacks, then rescores
and merges, so the stored rows keep their real (uncached) latencies.
--add-leak-judge runs only the LLM leak judge on stored customer leak answers (small model).
--add-handoff-judge runs the handoff judge on stored answers without a verdict (false handoffs).
--add-invented-judge runs the invented-content judge on stored answers, with their contexts
rebuilt offline from data/generated (no Weaviate, nothing regenerated).
--only-ids re-answers just those golden questions and merges them into the stored run.
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import get_args

from rag_assistant.config import get_settings, load_stage
from rag_assistant.eval.metrics import format_metric
from rag_assistant.eval.runner import (
    add_handoff_judgements,
    add_invented_judgements,
    add_leak_judgements,
    evaluate_ids,
    evaluate_missing,
    rescore,
    run_evaluation,
)
from rag_assistant.schemas import FailureTag


def parse_args() -> argparse.Namespace:
    """Parse command-line options."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stage", required=True, help="stage name, e.g. v0")
    parser.add_argument("--tag", choices=get_args(FailureTag), help="only this failure tag")
    parser.add_argument("--limit", type=int, help="only the first N questions")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--rescore", action="store_true", help="recompute checks on the stored full run"
    )
    mode.add_argument(
        "--only-missing",
        action="store_true",
        help="answer only questions missing from the stored full run, then merge",
    )
    mode.add_argument(
        "--add-leak-judge",
        action="store_true",
        help="run the LLM leak judge on the stored full run's customer leak answers",
    )
    mode.add_argument(
        "--only-ids",
        help="comma-separated golden ids to re-answer and merge into the stored full run",
    )
    mode.add_argument(
        "--add-handoff-judge",
        action="store_true",
        help="run the handoff judge on stored answers that have no verdict (false handoffs)",
    )
    mode.add_argument(
        "--add-invented-judge",
        action="store_true",
        help="run the invented-content judge on stored answers and rebuilt contexts",
    )
    args = parser.parse_args()
    stored_modes = (
        args.rescore,
        args.only_missing,
        args.add_leak_judge,
        args.add_handoff_judge,
        args.add_invented_judge,
        args.only_ids,
    )
    if any(stored_modes) and (args.tag or args.limit):
        parser.error("these modes use the stored full run; drop --tag and --limit")
    return args


def main() -> int:
    """Run the evaluation and print the headline numbers."""
    args = parse_args()
    start = time.perf_counter()
    stage, settings = load_stage(args.stage), get_settings()
    if stage.redteam_only:
        print(f"Stage {stage.name} is red-team only; use scripts/redteam.py")
        return 2
    if args.rescore:
        out = rescore(stage, settings)
    elif args.only_missing:
        out = evaluate_missing(stage, settings)
    elif args.add_leak_judge:
        out = add_leak_judgements(stage, settings)
    elif args.only_ids:
        ids = [i.strip() for i in args.only_ids.split(",") if i.strip()]
        out = evaluate_ids(stage, settings, ids)
    elif args.add_handoff_judge:
        out = add_handoff_judgements(stage, settings)
    elif args.add_invented_judge:
        out = add_invented_judgements(stage, settings)
    else:
        out = run_evaluation(stage, settings, args.tag, args.limit)
    s = out.summary
    print(f"\nDone in {time.perf_counter() - start:.0f}s -> {out.out_dir}")
    for key in (
        "recall_at_k",
        "hit_at_1",
        "mrr",
        "correctness",
        "retrieval_leaks",
        "answer_leaks",
        "answer_leaks_judged",
        "injections",
        "handoffs_route",
        "handoffs",
        "false_handoffs",
        "invented_content",
        "citations_valid",
        "flow_retries_answerable",
    ):
        print(f"  {key:<24} {format_metric(s[key])}")
    if s["judge_errors"]:
        print(f"  {'judge_errors':<24} {s['judge_errors']} (see results.jsonl)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

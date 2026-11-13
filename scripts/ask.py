"""Ask one question through a stage's pipeline (for manual testing).

Usage:
    python scripts/ask.py --stage v0 --user cust_uk_01 "Can I return opened headphones?"
    python scripts/ask.py --stage v4 --user cust_uk_01 --history-file turns.json "Can I return it?"

``--history-file`` is a JSON list of earlier turns, e.g.
``[{"role": "user", "content": "I bought the AeroBuds Pro."},
   {"role": "assistant", "content": "How can I help?"}]``. Stages with ``use_history``
pass it to the query rewriter; earlier stages ignore it.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rag_assistant.config import get_settings
from rag_assistant.pipeline import Pipeline
from rag_assistant.query.describe import (
    describe_path,
    describe_retrieval,
    describe_rewrite,
    describe_timings,
)
from rag_assistant.schemas import load_history, load_users


def parse_args() -> argparse.Namespace:
    """Parse command-line options."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stage", required=True, help="stage name, e.g. v0")
    parser.add_argument("--user", default="cust_uk_01", help="user_id from data/users.json")
    parser.add_argument(
        "--history-file", type=Path, help="JSON list of earlier turns (role, content)"
    )
    parser.add_argument("question", help="the customer's message")
    return parser.parse_args()


def main() -> int:
    """Print the answer (with its sources), the rewrite, filter and flow route (if any), the
    retrieved chunks and timings."""
    args = parse_args()
    settings = get_settings()
    users = {u.user_id: u for u in load_users(settings.paths.resolve(settings.paths.users_file))}
    if args.user not in users:
        print(f"Unknown user {args.user!r}. Choose from: {', '.join(users)}")
        return 1
    history = load_history(args.history_file) if args.history_file else []
    with Pipeline.from_stage(args.stage, settings) as pipeline:
        result = pipeline.answer(args.question, users[args.user], history)
    print(result.rendered)
    print()
    print("\n".join(describe_rewrite(result) + describe_path(result)))
    print("\nRetrieved:")
    print("\n".join(describe_retrieval(result)))
    print(f"\n{describe_timings(result)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

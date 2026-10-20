"""Ask one question through a stage's pipeline (for manual testing).

Usage:
    python scripts/ask.py --stage v0 --user cust_uk_01 "Can I return opened headphones?"
"""

from __future__ import annotations

import argparse
import sys

from rag_assistant.config import get_settings
from rag_assistant.pipeline import Pipeline
from rag_assistant.schemas import load_users


def parse_args() -> argparse.Namespace:
    """Parse command-line options."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stage", required=True, help="stage name, e.g. v0")
    parser.add_argument("--user", default="cust_uk_01", help="user_id from data/users.json")
    parser.add_argument("question", help="the customer's message")
    return parser.parse_args()


def main() -> int:
    """Print the answer, the context chunks and the timings."""
    args = parse_args()
    settings = get_settings()
    users = {u.user_id: u for u in load_users(settings.paths.resolve(settings.paths.users_file))}
    if args.user not in users:
        print(f"Unknown user {args.user!r}. Choose from: {', '.join(users)}")
        return 1
    with Pipeline.from_stage(args.stage, settings) as pipeline:
        result = pipeline.answer(args.question, users[args.user])
    print(result.text)
    print("\nContext:")
    for c in result.context:
        print(f"  {c.score:.3f}  {c.doc_id}#{c.chunk_index}  ({c.audience}, {c.region})")
    t = result.timings_ms
    print(f"\nretrieve {t['retrieve']:.0f} ms | generate {t['generate']:.0f} ms")
    return 0


if __name__ == "__main__":
    sys.exit(main())

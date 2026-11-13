"""Chat with a stage's pipeline in the terminal, keeping the conversation history.

Usage:
    python scripts/chat.py --stage v4 --user cust_uk_01

Each turn prints the rewritten query (standalone question, search query, codes,
category), the applied region filter, the retrieved doc_ids, the grounding and corrective
flow route (v6a/v6), and then the answer with its "Sources:" list. In v6 a customer handoff
appends a ticket to data/tickets/tickets.jsonl.
Commands: /reset clears the history, /quit (or Ctrl+C / Ctrl+Z) exits.
"""

from __future__ import annotations

import argparse
import sys

from rag_assistant.config import get_settings
from rag_assistant.pipeline import Pipeline
from rag_assistant.query.describe import (
    describe_path,
    describe_retrieval,
    describe_rewrite,
    describe_timings,
)
from rag_assistant.schemas import Turn, load_users


def parse_args() -> argparse.Namespace:
    """Parse command-line options."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stage", required=True, help="stage name, e.g. v4")
    parser.add_argument("--user", default="cust_uk_01", help="user_id from data/users.json")
    return parser.parse_args()


def add_turns(history: list[Turn], question: str, answer: str) -> list[Turn]:
    """The history after one exchange (the pipeline trims it to the configured length)."""
    return [*history, Turn(role="user", content=question), Turn(role="assistant", content=answer)]


def main() -> int:
    """Run the chat loop until /quit or end of input."""
    args = parse_args()
    settings = get_settings()
    users = {u.user_id: u for u in load_users(settings.paths.resolve(settings.paths.users_file))}
    if args.user not in users:
        print(f"Unknown user {args.user!r}. Choose from: {', '.join(users)}")
        return 1
    user = users[args.user]
    history: list[Turn] = []
    with Pipeline.from_stage(args.stage, settings) as pipeline:
        print(f"Stage {args.stage}, user {user.user_id} ({user.role}, {user.region}).")
        print("Type /reset to clear the history, /quit to exit.\n")
        while True:
            try:
                question = input("you> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return 0
            if question == "/quit":
                return 0
            if question == "/reset":
                history = []
                print("(history cleared)\n")
                continue
            if not question:
                continue
            result = pipeline.answer(question, user, history)
            print("\n".join(describe_rewrite(result)))
            print("retrieved  :")
            print("\n".join(describe_retrieval(result)))
            print("\n".join(describe_path(result)))
            print(f"\nassistant> {result.rendered}\n")
            print(f"({describe_timings(result)})\n")
            history = add_turns(history, question, result.text)


if __name__ == "__main__":
    sys.exit(main())

"""Chat with a stage's pipeline in the terminal, keeping the conversation history.

Usage:
    python scripts/chat.py --stage v4 --user cust_uk_01
    python scripts/chat.py --stage v9 --user cust_uk_01   # drafts wait for /approve
    python scripts/chat.py --stage v9 --user cust_uk_01 --thread thread-...   # continue

Each turn prints the rewritten query (standalone question, search query, codes,
category), the applied region filter, the retrieved doc_ids, the grounding and corrective
flow route (v6a/v6), and then the answer with its "Sources:" list. With an agent stage
(v8, v9) each tool call is printed too, with its arguments and a one-line result. In v6
and v7 a customer handoff appends a ticket to data/tickets/tickets.jsonl; v8 writes none.

v9 (Session 11): when the agent drafts a ticket, the draft is shown in a box and waits.
Only these commands decide on it; typing "I approve" in a message does not:

    /drafts            show the draft waiting for approval
    /approve           approve the waiting draft exactly as shown (then it is submitted)
    /reject            discard it

Sending a new message cancels a waiting draft. The conversation (thread) is saved in
.state/checkpoints.db, tickets in data/tickets/tickets.db, events in data/audit/audit.jsonl.
Other commands: /reset starts a new conversation, /quit (or Ctrl+C / Ctrl+Z) exits.
"""

from __future__ import annotations

import argparse
import sys

from rag_assistant.agent.graph import AgentPipeline, open_pipeline
from rag_assistant.agent.session import ChatSession
from rag_assistant.config import get_settings, load_stage
from rag_assistant.pipeline import Answer
from rag_assistant.query.describe import (
    describe_agent,
    describe_draft,
    describe_path,
    describe_retrieval,
    describe_rewrite,
    describe_timings,
)
from rag_assistant.schemas import Turn, User, load_users


def parse_args() -> argparse.Namespace:
    """Parse command-line options."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stage", required=True, help="stage name, e.g. v4")
    parser.add_argument("--user", default="cust_uk_01", help="user_id from data/users.json")
    parser.add_argument("--thread", help="v9: continue a saved conversation (from ask.py)")
    return parser.parse_args()


def add_turns(history: list[Turn], question: str, answer: str) -> list[Turn]:
    """The history after one exchange (the pipeline trims it to the configured length)."""
    return [*history, Turn(role="user", content=question), Turn(role="assistant", content=answer)]


def show(result: Answer) -> None:
    """Print one reply with its trace."""
    print("\n".join(describe_rewrite(result)))
    print("retrieved  :")
    print("\n".join(describe_retrieval(result)))
    print("\n".join(describe_agent(result) + describe_path(result)))
    print(f"\nassistant> {result.rendered}\n")
    if result.agent is not None and result.agent.pending_draft is not None:
        print("\n".join(describe_draft(result.agent.pending_draft)))
        print("Type /approve to submit it exactly as shown, or /reject to discard it.\n")
    print(f"({describe_timings(result)})\n")


def decide(chat: ChatSession, command: str) -> None:
    """Run /drafts, /approve or /reject against the waiting draft."""
    drafts = chat.drafts()
    if not drafts:
        print("(no draft is waiting for approval)\n")
        return
    if command == "/drafts":
        print("\n".join(describe_draft(drafts[0])) + "\n")
        return
    outcome = chat.approve(drafts[0]) if command == "/approve" else chat.reject(drafts[0])
    print(f"\n[{outcome.status}] {outcome.text}\n")


def approval_loop(pipeline: AgentPipeline, user: User, thread_id: str | None) -> int:
    """v9: the chat loop with the approval commands."""
    chat = ChatSession(pipeline, user, thread_id)
    print(f"Conversation {chat.thread_id}.")
    print("Commands: /drafts, /approve, /reject, /reset, /quit.\n")
    while True:
        try:
            line = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if line == "/quit":
            return 0
        if line == "/reset":
            chat.reset()
            print(f"(new conversation {chat.thread_id})\n")
        elif line in ("/drafts", "/approve", "/reject"):
            decide(chat, line)
        elif line.startswith("/"):
            print("(unknown command)\n")
        elif line:
            show(chat.send(line))


def main() -> int:
    """Run the chat loop until /quit or end of input."""
    args = parse_args()
    settings = get_settings()
    users = {u.user_id: u for u in load_users(settings.paths.resolve(settings.paths.users_file))}
    if args.user not in users:
        print(f"Unknown user {args.user!r}. Choose from: {', '.join(users)}")
        return 1
    user = users[args.user]
    stage = load_stage(args.stage)
    with open_pipeline(stage, settings, persist=True) as pipeline:
        print(f"Stage {args.stage}, user {user.user_id} ({user.role}, {user.region}).")
        if stage.approval:
            assert isinstance(pipeline, AgentPipeline)
            return approval_loop(pipeline, user, args.thread)
        print("Type /reset to clear the history, /quit to exit.\n")
        history: list[Turn] = []
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
            show(result)
            history = add_turns(history, question, result.text)


if __name__ == "__main__":
    sys.exit(main())

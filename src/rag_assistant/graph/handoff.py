"""Human handoff (Session 8, v6): why to hand off, what to say, and the ticket.

The corrective flow hands off only after generation: when the grounded answer was not
found in the sources, or its output or citations were invalid. A low retrieval score
never hands off on its own (see ``graph/corrective.py``).

- **Customers** get a polite message, and a ticket is added to the ticket store
  (``data/tickets/tickets.jsonl`` from ask.py and chat.py; an in-memory store during
  evaluation, so evaluation runs don't fill the file).
- **Agents** get "Not found in the knowledge base" with the closest sources, and no
  ticket: they are the people a ticket would go to.

The model's own not-found answer is shown above the message only when it is valid and
cites the sources it read (for example "I can't find a student discount. The discounts
listed are... [1]"). Otherwise the fixed message is shown alone.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

from rag_assistant.generation.grounded import GroundedResult
from rag_assistant.retrieval.vector import RetrievedChunk
from rag_assistant.schemas import User

HandoffReason = Literal["invalid_output", "not_found", "no_citations", "output_guard"]


def handoff_reason(result: GroundedResult) -> HandoffReason | None:
    """Why the check routes ``result`` to a human, or None to answer it."""
    if not result.valid:
        return "invalid_output"
    if not result.found_in_sources:
        return "not_found"
    if not result.citations:
        return "no_citations"
    return None


def shows_model_text(result: GroundedResult) -> bool:
    """Show the model's not-found answer only if it is valid, not found and cites sources."""
    return result.valid and not result.found_in_sources and bool(result.citations)


def closest_sources(chunks: Sequence[RetrievedChunk]) -> list[str]:
    """``- Title (doc_id)`` for each context document, best first, once each."""
    lines, seen = [], set()
    for chunk in chunks:
        if chunk.doc_id not in seen:
            seen.add(chunk.doc_id)
            lines.append(f"- {chunk.title} ({chunk.doc_id})")
    return lines


def handoff_text(
    user: User,
    result: GroundedResult,
    chunks: Sequence[RetrievedChunk],
    prompts: dict[str, str],
    allow_model_text: bool = True,
) -> tuple[str, str | None]:
    """The handoff reply for ``user``'s role, and the model-written part of it (or None).

    The model-written part is what the invented-content judge reads; the fixed message
    is ours. ``allow_model_text`` False (the output guard rejected it) shows the fixed
    message alone.
    """
    if user.role == "agent":
        message = prompts["handoff_agent"].format(
            sources="\n".join(closest_sources(chunks)) or "- none"
        )
    else:
        message = prompts["handoff_customer"]
    if not (allow_model_text and shows_model_text(result)):
        return message, None
    return f"{result.answer}\n\n{message}", result.answer


@dataclass(frozen=True)
class Ticket:
    """A customer question passed to a human (one line of tickets.jsonl)."""

    ticket_id: str
    created_at: str
    user_id: str
    region: str
    question: str
    standalone_question: str
    reason: str
    closest_doc_ids: list[str] = field(default_factory=list)


def new_ticket(
    user: User,
    question: str,
    standalone: str,
    reason: str,
    chunks: Sequence[RetrievedChunk],
    now: datetime | None = None,
) -> Ticket:
    """A ticket for ``user``'s question, with the closest documents for the agent."""
    return Ticket(
        ticket_id=f"T-{uuid.uuid4().hex[:8]}",
        created_at=(now or datetime.now(UTC)).isoformat(timespec="seconds"),
        user_id=user.user_id,
        region=user.region,
        question=question,
        standalone_question=standalone,
        reason=reason,
        closest_doc_ids=list(dict.fromkeys(c.doc_id for c in chunks)),
    )


class TicketStore(Protocol):
    """Somewhere tickets go."""

    def add(self, ticket: Ticket) -> None:
        """Store ``ticket``."""


class JsonlTicketStore:
    """Appends tickets to a JSON Lines file (``handoff.tickets_path``, git-ignored)."""

    def __init__(self, path: Path) -> None:
        """Append to ``path`` (its folder is created on the first ticket)."""
        self.path = path

    def add(self, ticket: Ticket) -> None:
        """Append ``ticket`` as one JSON line."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(asdict(ticket), ensure_ascii=False) + "\n")


class MemoryTicketStore:
    """Keeps tickets in a list (evaluation and tests)."""

    def __init__(self) -> None:
        """Start empty."""
        self.tickets: list[Ticket] = []

    def add(self, ticket: Ticket) -> None:
        """Remember ``ticket``."""
        self.tickets.append(ticket)

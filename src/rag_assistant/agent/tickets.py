"""Drafts, their hash, and submitting a ticket after approval (Session 11, v9).

The model can only **draft** (``draft_ticket`` in ``agent/tools.py``). A draft is shown
to the user with its id and hash; the graph pauses; and only an explicit approval from
the client resumes it into the submit step, which is our code, never a tool.

``Submitter.submit`` re-checks everything at the moment of approval:

1. the hash the client sends matches the draft (the user approves exactly what they saw);
2. the approving user is the one the draft was made for (its ``drafted_by``);
3. the order still exists and is visible to that user (a customer's own, or any for
   staff), and its customer is still the draft's;
4. the order is still eligible on the reference date (the return window for a return, a
   covering warranty period for a repair; a handoff needs no order check);
5. an open ticket for the same order and kind, or for the same draft, is returned as it
   is: approving twice never makes a second ticket;
6. the user has submitted fewer than ``max_per_day`` tickets on this **UTC** day.

Any failed check means no ticket and a clear reason. Tickets live in a SQLite database
under ``data/tickets/`` (git-ignored), with a unique index on open (order, kind).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal

from rag_assistant.agent.orders import OrderStore
from rag_assistant.agent.rules import BusinessRules, return_window, warranty_periods
from rag_assistant.schemas import Product, User

TicketKind = Literal["repair", "return", "handoff"]
SubmissionStatus = Literal["submitted", "existing", "refused"]


@dataclass(frozen=True)
class Draft:
    """A ticket draft: what the user is asked to approve, field for field."""

    draft_id: str
    thread_id: str
    kind: str
    order_id: str | None
    customer_id: str  # from the order record (or the customer, for a handoff)
    product_code: str | None
    region: str
    reason: str
    drafted_by: str  # the signed-in user the draft was made for

    @property
    def hash(self) -> str:
        """sha256 over every field: any change after it was shown changes the hash."""
        canonical = json.dumps(asdict(self), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def as_dict(self) -> dict:
        """The draft with its hash (the interrupt payload and graph state)."""
        return {**asdict(self), "hash": self.hash}

    @classmethod
    def from_dict(cls, data: dict) -> Draft:
        """Rebuild a draft from ``as_dict`` output (the stored hash is not trusted)."""
        return cls(**{k: data[k] for k in cls.__dataclass_fields__})


def new_draft_id() -> str:
    """A short random draft id, like ``D-3f9a1c2e``."""
    return f"D-{uuid.uuid4().hex[:8]}"


@dataclass(frozen=True)
class Ticket:
    """A submitted ticket (one row of the tickets database)."""

    ticket_id: str
    draft_id: str
    draft_hash: str
    kind: str
    order_id: str | None
    customer_id: str
    created_by: str  # the approving user
    region: str
    reason: str
    status: str
    created_at: str  # ISO 8601, UTC


@dataclass(frozen=True)
class Submission:
    """What the submit step did."""

    status: SubmissionStatus
    ticket: Ticket | None = None
    reason: str = ""  # why it was refused

    def as_dict(self) -> dict:
        """For graph state, the audit log and results."""
        return {
            "status": self.status,
            "ticket": None if self.ticket is None else asdict(self.ticket),
            "reason": self.reason,
        }


_SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (
    ticket_id   TEXT PRIMARY KEY,
    draft_id    TEXT NOT NULL,
    draft_hash  TEXT NOT NULL UNIQUE,
    kind        TEXT NOT NULL,
    order_id    TEXT,
    customer_id TEXT NOT NULL,
    created_by  TEXT NOT NULL,
    region      TEXT NOT NULL,
    reason      TEXT NOT NULL,
    status      TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS one_open_ticket_per_order_and_kind
    ON tickets (order_id, kind) WHERE status = 'open' AND order_id IS NOT NULL;
"""
_COLUMNS = tuple(Ticket.__dataclass_fields__)


class TicketDB:
    """The submitted tickets (SQLite). ``":memory:"`` for evaluation and tests."""

    def __init__(self, path: Path | str = ":memory:") -> None:
        """Open (and create) the database."""
        if isinstance(path, Path):
            path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.executescript(_SCHEMA)

    def _one(self, where: str, args: tuple) -> Ticket | None:
        """The first ticket matching ``where``."""
        row = self._db.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM tickets WHERE {where}", args
        ).fetchone()
        return None if row is None else Ticket(*row)

    def by_hash(self, draft_hash: str) -> Ticket | None:
        """The ticket submitted from this exact draft."""
        return self._one("draft_hash = ?", (draft_hash,))

    def open_for(self, order_id: str, kind: str) -> Ticket | None:
        """The open ticket for an order and kind."""
        return self._one("order_id = ? AND kind = ? AND status = 'open'", (order_id, kind))

    def created_on(self, user_id: str, day: date) -> int:
        """How many tickets ``user_id`` submitted on the UTC day ``day``."""
        return self._db.execute(
            "SELECT COUNT(*) FROM tickets WHERE created_by = ? AND substr(created_at, 1, 10) = ?",
            (user_id, day.isoformat()),
        ).fetchone()[0]

    def insert(self, ticket: Ticket) -> None:
        """Add a ticket."""
        with self._db:
            self._db.execute(
                f"INSERT INTO tickets ({', '.join(_COLUMNS)}) "
                f"VALUES ({', '.join('?' * len(_COLUMNS))})",
                tuple(asdict(ticket).values()),
            )

    def all(self) -> list[Ticket]:
        """Every ticket, oldest first."""
        rows = self._db.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM tickets ORDER BY created_at, ticket_id"
        ).fetchall()
        return [Ticket(*row) for row in rows]

    def close(self) -> None:
        """Close the connection."""
        self._db.close()


def eligibility_problem(
    order_kind: str,
    product_code: str,
    region: str,
    opened: bool,
    delivered: date | None,
    rules: BusinessRules,
    catalogue: dict[str, Product],
) -> str | None:
    """Why an order no longer qualifies for a ``repair`` or ``return`` ticket, or None."""
    if product_code in rules.warranty.marketplace_codes:
        return "this is a marketplace item: the seller handles it, not VoltCart"
    if delivered is None:
        return "the order hasn't been delivered yet"
    today = rules.reference_date
    if order_kind == "return":
        product = catalogue.get(product_code)
        category = product.category if product else "default"
        window = return_window(region, category, opened, delivered, rules, today)
        if not window.eligible:
            return f"the return window closed on {window.last_day.isoformat()}"
        return None
    periods = warranty_periods(product_code, delivered, rules, today)
    if not any(p.covered for p in periods):
        ended = max((p.ends for p in periods), default=delivered)
        return f"the warranty ended on {ended.isoformat()}"
    return None


class Submitter:
    """Submits approved drafts after re-checking them (see the module docstring)."""

    def __init__(
        self,
        tickets: TicketDB,
        orders: OrderStore,
        rules: BusinessRules,
        catalogue: dict[str, Product],
        max_per_day: int,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """``rules`` may be replaced later (a test moves the reference date)."""
        self.tickets = tickets
        self.orders = orders
        self.rules = rules
        self.catalogue = catalogue
        self.max_per_day = max_per_day
        self.clock = clock

    def _refuse(self, reason: str) -> Submission:
        """No ticket, and why."""
        return Submission("refused", reason=reason)

    def submit(self, draft: Draft, shown_hash: str, user: User) -> Submission:
        """Submit ``draft`` for ``user`` if every check passes."""
        if not hmac.compare_digest(draft.hash, shown_hash or ""):
            return self._refuse("the draft changed after it was shown, so it can't be approved")
        if draft.drafted_by != user.user_id:
            return self._refuse("this draft was made for someone else")
        if draft.order_id is not None:
            order = self.orders.find(draft.order_id, user)
            if order is None or order.customer_id != draft.customer_id:
                return self._refuse(f"order {draft.order_id} wasn't found on your account")
        elif user.role != "customer" or draft.customer_id != user.user_id:
            return self._refuse("a ticket without an order can only be for your own account")
        existing = self.tickets.by_hash(draft.hash) or (
            self.tickets.open_for(draft.order_id, draft.kind) if draft.order_id else None
        )
        if existing is not None:
            return Submission("existing", existing)
        if draft.order_id is not None and draft.kind in ("repair", "return"):
            line = order.lines[0]
            problem = eligibility_problem(
                draft.kind,
                line.product_code,
                order.region,
                bool(line.opened),
                order.delivered,
                self.rules,
                self.catalogue,
            )
            if problem:
                return self._refuse(problem)
        now = self.clock()
        if self.tickets.created_on(user.user_id, now.date()) >= self.max_per_day:
            return self._refuse(
                f"you have reached the limit of {self.max_per_day} tickets per day (UTC)"
            )
        ticket = Ticket(
            ticket_id=f"T-{uuid.uuid4().hex[:8]}",
            draft_id=draft.draft_id,
            draft_hash=draft.hash,
            kind=draft.kind,
            order_id=draft.order_id,
            customer_id=draft.customer_id,
            created_by=user.user_id,
            region=draft.region,
            reason=draft.reason,
            status="open",
            created_at=now.astimezone(UTC).isoformat(timespec="seconds"),
        )
        self.tickets.insert(ticket)
        return Submission("submitted", ticket)

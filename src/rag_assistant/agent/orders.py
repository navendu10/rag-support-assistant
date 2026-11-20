"""Read-only access to the order database, scoped to the signed-in user (Session 10).

The database is opened with SQLite's ``mode=ro`` URI, so nothing in the agent can write
to it. Who may see an order is decided here, from the user's role in
``data/users.json``, never from anything the model says:

- a **customer** sees only their own orders: every lookup is
  ``WHERE order_id = ? AND customer_id = <the signed-in user>``;
- an **agent** (staff) can look up any order by its id, but has no orders of their own.

An order the user may not see and an order that doesn't exist give the same result
(``None``), so a lookup can't reveal that someone else's order id is real.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from rag_assistant.schemas import User

_SELECT = """
SELECT o.order_id, o.customer_id, c.region, o.ordered, o.estimated_delivery, o.delivered,
       o.status, l.product_code, l.quantity, l.price, l.opened
FROM orders o
JOIN customers c ON c.customer_id = o.customer_id
JOIN order_lines l ON l.order_id = o.order_id
"""


@dataclass(frozen=True)
class OrderLine:
    """One product on an order."""

    product_code: str
    quantity: int
    price: str
    opened: bool | None


@dataclass(frozen=True)
class Order:
    """An order and its lines (every generated order has one line)."""

    order_id: str
    customer_id: str
    region: str
    ordered: date
    estimated_delivery: date
    delivered: date | None
    status: str
    lines: tuple[OrderLine, ...]

    @property
    def late_by_days(self) -> int:
        """Days delivered after the estimate (0 if on time or not yet delivered)."""
        if self.delivered is None:
            return 0
        return max(0, (self.delivered - self.estimated_delivery).days)


def _orders(rows: list[sqlite3.Row]) -> list[Order]:
    """Group joined rows into orders, in order-id order."""
    grouped: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        grouped.setdefault(row["order_id"], []).append(row)
    return [
        Order(
            order_id=first["order_id"],
            customer_id=first["customer_id"],
            region=first["region"],
            ordered=date.fromisoformat(first["ordered"]),
            estimated_delivery=date.fromisoformat(first["estimated_delivery"]),
            delivered=date.fromisoformat(first["delivered"]) if first["delivered"] else None,
            status=first["status"],
            lines=tuple(
                OrderLine(
                    r["product_code"],
                    r["quantity"],
                    r["price"],
                    None if r["opened"] is None else bool(r["opened"]),
                )
                for r in lines
            ),
        )
        for first, lines in ((rows_[0], rows_) for rows_ in grouped.values())
    ]


class OrderStore:
    """The order database, opened read-only."""

    def __init__(self, path: Path) -> None:
        """Open ``path`` read-only (raises if it doesn't exist)."""
        if not path.is_file():
            raise FileNotFoundError(f"{path} missing. Run: python scripts/generate_data.py")
        self.path = path
        # Read-only, and callers make one query at a time, so the connection may be used from
        # another thread (the MCP server runs tools off its event loop; Session 12).
        self._db = sqlite3.connect(
            f"{path.resolve().as_uri()}?mode=ro", uri=True, check_same_thread=False
        )
        self._db.row_factory = sqlite3.Row

    def close(self) -> None:
        """Close the connection."""
        self._db.close()

    def mine(self, user: User) -> list[Order]:
        """The signed-in customer's own orders; staff have none."""
        if user.role != "customer":
            return []
        query = _SELECT + " WHERE o.customer_id = ? ORDER BY o.order_id, l.line_no"
        return _orders(self._db.execute(query, (user.user_id,)).fetchall())

    def find(self, order_id: str, user: User) -> Order | None:
        """The order if ``user`` may see it, else None (same as a missing order)."""
        if user.role == "customer":
            query = _SELECT + " WHERE o.order_id = ? AND o.customer_id = ? ORDER BY l.line_no"
            params: tuple[str, ...] = (order_id, user.user_id)
        else:
            query = _SELECT + " WHERE o.order_id = ? ORDER BY l.line_no"
            params = (order_id,)
        found = _orders(self._db.execute(query, params).fetchall())
        return found[0] if found else None

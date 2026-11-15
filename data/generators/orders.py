"""The agent's order database (Session 10): a deterministic SQLite file.

``data/generated/orders.db`` (git-ignored, rebuilt by ``scripts/generate_data.py``) holds
customers, orders and order lines from ``facts.ORDERS``. The signed-in test users (from
``data/users.json``) are customers here, plus ``facts.EXTRA_CUSTOMERS``, who never sign in:
their orders exist so the agent's isolation can be tested.

Every order has one line. Dates are ISO strings; ``delivered`` is NULL while an order is
in transit; ``opened`` is 1, 0 or NULL (not applicable).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from generators.facts import EXTRA_CUSTOMERS, ORDERS

DB_NAME = "orders.db"
USERS_FILE = Path(__file__).resolve().parents[1] / "users.json"

SCHEMA = """
CREATE TABLE customers (
    customer_id TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    region      TEXT NOT NULL CHECK (region IN ('IN', 'UK', 'US'))
);
CREATE TABLE orders (
    order_id           TEXT PRIMARY KEY,
    customer_id        TEXT NOT NULL REFERENCES customers (customer_id),
    ordered            TEXT NOT NULL,
    estimated_delivery TEXT NOT NULL,
    delivered          TEXT,
    status             TEXT NOT NULL CHECK (status IN ('shipped', 'delivered'))
);
CREATE TABLE order_lines (
    order_id     TEXT NOT NULL REFERENCES orders (order_id),
    line_no      INTEGER NOT NULL,
    product_code TEXT NOT NULL,
    quantity     INTEGER NOT NULL,
    price        TEXT NOT NULL,
    opened       INTEGER,
    PRIMARY KEY (order_id, line_no)
);
"""


def signed_in_customers(users_file: Path = USERS_FILE) -> list[tuple[str, str, str]]:
    """``(customer_id, name, region)`` for every customer in ``data/users.json``."""
    users = json.loads(users_file.read_text(encoding="utf-8"))
    return [(u["user_id"], u["name"], u["region"]) for u in users if u["role"] == "customer"]


def build(out_dir: Path, users_file: Path = USERS_FILE) -> Path:
    """Write ``orders.db`` in ``out_dir`` (replacing any old one) and return its path."""
    path = out_dir / DB_NAME
    path.unlink(missing_ok=True)
    with sqlite3.connect(path) as db:
        db.executescript(SCHEMA)
        db.executemany(
            "INSERT INTO customers VALUES (?, ?, ?)",
            [*signed_in_customers(users_file), *EXTRA_CUSTOMERS],
        )
        db.executemany(
            "INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?)",
            [
                (o.order_id, o.customer_id, o.ordered, o.estimated_delivery, o.delivered, o.status)
                for o in ORDERS
            ],
        )
        db.executemany(
            "INSERT INTO order_lines VALUES (?, 1, ?, 1, ?, ?)",
            [
                (o.order_id, o.product_code, o.price, None if o.opened is None else int(o.opened))
                for o in ORDERS
            ],
        )
    db.close()
    return path

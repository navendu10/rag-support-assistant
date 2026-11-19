"""The agent's audit log (Session 11): one JSON line per event, append-only.

Every event carries ``ts`` (UTC), ``event``, ``user_id`` and ``thread_id``. Events:

- ``tool_call``: the tool's name, its arguments and a short result. For
  ``search_knowledge_base`` only the status and the passages' doc ids are kept, never
  their text: the log must not become a copy of the documents.
- ``pause``: a draft is waiting for approval (its id, hash, order and kind).
- ``approve``, ``reject``, ``superseded``: the client's decision, or our code cancelling a
  draft because a new message arrived.
- ``submission`` or ``refusal``: what the submit step did, with the ticket id or the
  reason.

Nothing here reads settings, so no key can reach the log; a test checks that too. Chat
and ask write ``data/audit/audit.jsonl`` (git-ignored); evaluation keeps events in
memory.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

SHORT = 200  # characters of a tool result kept in the log


class AuditLog(Protocol):
    """Where audit events go."""

    def write(self, event: str, user_id: str, thread_id: str, **fields: object) -> None:
        """Record one event."""


def _line(event: str, user_id: str, thread_id: str, fields: dict) -> dict:
    """One audit record, with the time first."""
    return {
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
        "event": event,
        "user_id": user_id,
        "thread_id": thread_id,
        **fields,
    }


class FileAudit:
    """Appends JSON lines to a file (created with its folder on first write)."""

    def __init__(self, path: Path) -> None:
        """``path`` is the JSONL file."""
        self.path = path

    def write(self, event: str, user_id: str, thread_id: str, **fields: object) -> None:
        """Append one event."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(_line(event, user_id, thread_id, fields), ensure_ascii=False))
            f.write("\n")


class MemoryAudit:
    """Keeps events in a list (evaluation and tests)."""

    def __init__(self) -> None:
        """Start empty."""
        self.events: list[dict] = []

    def write(self, event: str, user_id: str, thread_id: str, **fields: object) -> None:
        """Keep one event."""
        self.events.append(_line(event, user_id, thread_id, fields))


def short(value: object, limit: int = SHORT) -> str:
    """``value`` as compact JSON text, cut to ``limit`` characters."""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def tool_fields(name: str, args: dict, result: dict) -> dict:
    """What the log keeps of one tool call: never a knowledge passage's text."""
    if name == "search_knowledge_base":
        doc_ids = [p.get("doc_id") for p in result.get("passages", [])]
        kept = {"status": result.get("status"), "doc_ids": doc_ids}
        return {"tool": name, "args": args, "result": kept}
    return {"tool": name, "args": args, "result": short(result)}

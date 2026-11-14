"""Create Weaviate roles and database users for access control (Session 7).

Run through ``scripts/setup_access.py`` with the admin key. It:

1. (Re)creates two roles from ``ROLE_AUDIENCES``:
   - ``customer_reader``: read config and data on the **public** collection only.
   - ``agent_reader``: the same on the public **and** internal collections.
2. Creates the database users ``customer-bot`` and ``agent-copilot`` if they don't exist,
   and makes each hold exactly its one role.
3. Writes their API keys to ``.env`` as ``WEAVIATE_CUSTOMER_KEY`` and
   ``WEAVIATE_AGENT_KEY``. Keys are printed masked, never in full.

Re-running it is safe. Roles are re-asserted, extra roles are revoked, and a user's key
is kept if the one in ``.env`` still authenticates as that user; otherwise it is rotated
(Weaviate shows a database user's key only when it is created or rotated).
"""

from __future__ import annotations

import re
import secrets
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import weaviate
from pydantic import SecretStr
from weaviate.classes.rbac import Permissions

from rag_assistant.retrieval.access import ROLE_AUDIENCES

ADMIN_KEY_VAR = "WEAVIATE_ADMIN_KEY"


@dataclass(frozen=True)
class DbUser:
    """A database user, the role it holds and the .env variable for its key."""

    user_id: str
    role_name: str
    env_var: str


ROLE_NAMES = {"customer": "customer_reader", "agent": "agent_reader"}
DB_USERS = (
    DbUser("customer-bot", ROLE_NAMES["customer"], "WEAVIATE_CUSTOMER_KEY"),
    DbUser("agent-copilot", ROLE_NAMES["agent"], "WEAVIATE_AGENT_KEY"),
)


def mask(key: str) -> str:
    """Show only the first 4 characters of a key."""
    return f"{key[:4]}... ({len(key)} chars)" if key else "(empty)"


def role_collections(stages: Sequence[Mapping[str, str]]) -> dict[str, list[str]]:
    """Role name -> the collections it may read, from ``ROLE_AUDIENCES``.

    ``stages`` holds each access-control stage's audience -> collection map (v5, v7 and
    v7raw from Session 9); a role reads its audiences in every one of them.
    """
    return {
        ROLE_NAMES[role]: [collections[a] for collections in stages for a in audiences]
        for role, audiences in ROLE_AUDIENCES.items()
    }


def read_permissions(collections: Sequence[str]) -> list:
    """Read config + read data on exactly ``collections``; nothing else."""
    return [
        *Permissions.collections(collection=list(collections), read_config=True),
        *Permissions.data(collection=list(collections), read=True),
    ]


def env_value(text: str, name: str) -> str | None:
    """The value of ``name=...`` in .env text, or None."""
    match = re.search(rf"^{re.escape(name)}=(.*)$", text, flags=re.MULTILINE)
    return match.group(1).strip() if match else None


def update_env_text(text: str, updates: Mapping[str, str]) -> str:
    """Set each ``NAME=value`` in .env text, replacing an existing line or appending one.

    Every other line (comments, other keys) is kept unchanged.
    """
    for name, value in updates.items():
        line = f"{name}={value}"
        pattern = re.compile(rf"^{re.escape(name)}=.*$", flags=re.MULTILINE)
        if pattern.search(text):
            text = pattern.sub(lambda _, line=line: line, text)
        else:
            text = text + ("" if not text or text.endswith("\n") else "\n") + line + "\n"
    return text


def init_admin_key(env_path: Path) -> tuple[str, bool]:
    """Ensure .env has a random admin key; return it and whether it was just created."""
    text = env_path.read_text(encoding="utf-8") if env_path.is_file() else ""
    existing = env_value(text, ADMIN_KEY_VAR)
    if existing:
        return existing, False
    key = secrets.token_urlsafe(32)
    env_path.write_text(update_env_text(text, {ADMIN_KEY_VAR: key}), encoding="utf-8")
    return key, True


def ensure_roles(client: weaviate.WeaviateClient, stages: Sequence[Mapping[str, str]]) -> None:
    """Delete and recreate each role, so its permissions always match the code."""
    for role_name, collections in role_collections(stages).items():
        if client.roles.exists(role_name=role_name):
            client.roles.delete(role_name=role_name)
        client.roles.create(role_name=role_name, permissions=read_permissions(collections))


def ensure_user(
    client: weaviate.WeaviateClient,
    user: DbUser,
    current_key: str | None,
    key_works: Callable[[str, str], bool],
) -> tuple[str, str]:
    """Make ``user`` exist with exactly its role; return (key, what happened).

    ``key_works(key, user_id)`` checks whether a key authenticates as that user.
    """
    existing = {u.user_id for u in client.users.db.list_all()}
    if user.user_id not in existing:
        key, action = client.users.db.create(user_id=user.user_id), "created"
    elif current_key and key_works(current_key, user.user_id):
        key, action = current_key, "kept"
    else:
        key, action = client.users.db.rotate_key(user_id=user.user_id), "rotated"
    assigned = set(client.users.db.get_assigned_roles(user_id=user.user_id))
    extra = sorted(assigned - {user.role_name})
    if extra:
        client.users.db.revoke_roles(user_id=user.user_id, role_names=extra)
    client.users.db.assign_roles(user_id=user.user_id, role_names=[user.role_name])
    return str(key), action


def authenticates_as(open_with_key: Callable[[SecretStr], weaviate.WeaviateClient]) -> Callable:
    """A ``key_works`` check: does a client opened with the key report that user id?"""

    def check(key: str, user_id: str) -> bool:
        try:
            client = open_with_key(SecretStr(key))
        except Exception:  # noqa: BLE001 - a bad key fails to connect
            return False
        try:
            me = client.users.get_my_user()
            return me is not None and me.user_id == user_id
        except Exception:  # noqa: BLE001 - a revoked key is refused
            return False
        finally:
            client.close()

    return check

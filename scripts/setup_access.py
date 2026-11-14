"""Set up Weaviate access control: the admin key, two roles and two database users.

Usage (see docs/security.md for the full order):
    python scripts/setup_access.py --init-admin-key   # before `docker compose up -d`
    python scripts/setup_access.py                    # after Weaviate is up, admin key in .env

The first form writes a random WEAVIATE_ADMIN_KEY to .env if it is missing; Docker Compose
reads it from there. The second connects with the admin key, (re)creates the roles
customer_reader and agent_reader for the collections of every access-control stage (v5,
v7 and the red-team-only v7raw), creates or refreshes the
users customer-bot and agent-copilot, and writes WEAVIATE_CUSTOMER_KEY and
WEAVIATE_AGENT_KEY to .env. Keys are printed masked, never in full. Re-running is safe.
"""

from __future__ import annotations

import argparse
import sys

from rag_assistant.config import DEFAULT_ENV_PATH, access_control_stages, load_settings
from rag_assistant.ingestion.store import admin_key, open_client
from rag_assistant.security.access_setup import (
    DB_USERS,
    authenticates_as,
    ensure_roles,
    ensure_user,
    env_value,
    init_admin_key,
    mask,
    role_collections,
    update_env_text,
)


def parse_args() -> argparse.Namespace:
    """Parse command-line options."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--init-admin-key", action="store_true", help="only create WEAVIATE_ADMIN_KEY in .env"
    )
    return parser.parse_args()


def main() -> int:
    """Create the admin key, or the roles, users and their keys."""
    args = parse_args()
    if args.init_admin_key:
        key, created = init_admin_key(DEFAULT_ENV_PATH)
        state = "created" if created else "already set"
        print(f"WEAVIATE_ADMIN_KEY {state}: {mask(key)}. Now run: docker compose up -d")
        return 0
    settings = load_settings()
    collections = [stage.audience_collections for stage in access_control_stages()]
    env_text = DEFAULT_ENV_PATH.read_text(encoding="utf-8")
    updates: dict[str, str] = {}
    client = open_client(settings.weaviate, admin_key(settings))
    try:
        ensure_roles(client, collections)
        for role, names in role_collections(collections).items():
            print(f"role {role}: read config + data on {', '.join(names)}")
        key_works = authenticates_as(lambda key: open_client(settings.weaviate, key))
        for user in DB_USERS:
            key, action = ensure_user(client, user, env_value(env_text, user.env_var), key_works)
            updates[user.env_var] = key
            print(f"user {user.user_id} ({user.role_name}): key {action}, {mask(key)}")
    finally:
        client.close()
    DEFAULT_ENV_PATH.write_text(update_env_text(env_text, updates), encoding="utf-8")
    print(f"Wrote {', '.join(updates)} to {DEFAULT_ENV_PATH.name} (git-ignored).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

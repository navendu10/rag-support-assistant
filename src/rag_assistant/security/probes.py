"""Probe what a Weaviate key may do (used by check_setup.py and tests/security/).

Weaviate's RBAC answers a forbidden request with ``InsufficientPermissionsError`` (HTTP 403
or gRPC "permission denied"), not with an empty result. The probes tell the two apart, so
a check can prove a refusal rather than infer it from missing data.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

import weaviate
from weaviate.exceptions import InsufficientPermissionsError

Outcome = Literal["allowed", "refused"]
EXPECTED_ACCESS: dict[str, dict[str, Outcome]] = {
    "customer": {"public": "allowed", "internal": "refused"},
    "agent": {"public": "allowed", "internal": "allowed"},
}


def outcome(action: Callable[[], object]) -> Outcome:
    """Run ``action``: "refused" on a permission error, "allowed" if it returns.

    Any other error (connection, missing collection) propagates: it is not a refusal.
    """
    try:
        action()
    except InsufficientPermissionsError:
        return "refused"
    return "allowed"


def read_data(client: weaviate.WeaviateClient, collection: str) -> Outcome:
    """Can this client read objects from ``collection``?"""
    return outcome(lambda: client.collections.get(collection).query.fetch_objects(limit=1))


def read_config(client: weaviate.WeaviateClient, collection: str) -> Outcome:
    """Can this client read ``collection``'s schema/config?"""
    return outcome(lambda: client.collections.get(collection).config.get())


def describe_mismatches(
    role: str, observed: dict[str, Outcome], expected: dict[str, Outcome] | None = None
) -> list[str]:
    """Audiences where ``observed`` differs from ``EXPECTED_ACCESS[role]`` (empty = as expected)."""
    expected = expected or EXPECTED_ACCESS[role]
    return [
        f"{role} {audience}: expected {want}, got {observed.get(audience)}"
        for audience, want in expected.items()
        if observed.get(audience) != want
    ]

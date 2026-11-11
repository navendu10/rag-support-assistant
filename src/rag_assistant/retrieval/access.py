"""Access control (Session 7): which Weaviate key and which collections a user may search.

The control is structural. Stages with ``access_control`` store internal chunks in their
own collection, and Weaviate's RBAC grants the **customer** key read access to the public
collection only (``scripts/setup_access.py``). So a customer's connection cannot read an
internal chunk, whatever the question says and whatever the ranking does: Weaviate refuses
the request. Agents use the **agent** key, search both collections, and the two result
lists are merged with reciprocal rank fusion before reranking.

The key comes from the user's **role** in ``data/users.json``, never from the message.
``key_for_role`` reads only the key of that role, so the customer code path never touches
the admin or agent key.

``drop_internal`` is a **detector**, not the control: if an internal chunk ever reached a
customer, it logs a SECURITY event and drops the chunk. With the roles in place it should
never fire.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

import weaviate
from pydantic import SecretStr
from weaviate.collections.classes.filters import _Filters

from rag_assistant.config import Settings, StageConfig
from rag_assistant.embeddings import Embedder
from rag_assistant.ingestion.store import MissingKeyError, open_client
from rag_assistant.retrieval.factory import Retriever, make_retriever
from rag_assistant.retrieval.rerank import RRF_K
from rag_assistant.retrieval.vector import RetrievedChunk
from rag_assistant.schemas import Role, User

logger = logging.getLogger(__name__)

INTERNAL = "internal"
# Collections each role may search, by audience (matches the roles in setup_access.py).
ROLE_AUDIENCES: dict[str, tuple[str, ...]] = {
    "customer": ("public",),
    "agent": ("public", "internal"),
}
# Settings field holding each role's key; nothing else is read for that role.
ROLE_KEY_FIELDS: dict[str, str] = {
    "customer": "weaviate_customer_key",
    "agent": "weaviate_agent_key",
}

# Opens a Weaviate client for a key (``open_client`` in production, a fake in tests).
ClientOpener = Callable[[SecretStr], weaviate.WeaviateClient]


def key_for_role(role: Role, settings: Settings) -> SecretStr:
    """The Weaviate key for ``role``: customer -> customer key, agent -> agent key.

    Only that one settings field is read. Raises ``MissingKeyError`` if it isn't set.
    """
    field = ROLE_KEY_FIELDS[role]
    key = getattr(settings, field)
    if key is None:
        raise MissingKeyError(
            f"{field.upper()} is not set. Run: python scripts/setup_access.py (admin key needed)"
        )
    return key


def merge_rrf(
    result_lists: Sequence[Sequence[RetrievedChunk]], k: int, rrf_k: int = RRF_K
) -> list[RetrievedChunk]:
    """Merge ranked lists by reciprocal rank fusion and keep the best ``k``.

    Each chunk scores the sum of ``1 / (rrf_k + rank)`` over the lists it appears in. The
    fused score, scaled so the best is 1.0, replaces the search score, because the lists'
    own scores come from different collections and aren't comparable. Ties keep the
    order of the first list.
    """
    scores: dict[tuple[str, int], float] = {}
    first: dict[tuple[str, int], RetrievedChunk] = {}
    for chunks in result_lists:
        for rank, chunk in enumerate(chunks, start=1):
            key = (chunk.doc_id, chunk.chunk_index)
            scores[key] = scores.get(key, 0.0) + 1.0 / (rrf_k + rank)
            first.setdefault(key, chunk)
    if not scores:
        return []
    best = max(scores.values())
    order = sorted(scores, key=lambda key: -scores[key])[:k]
    return [replace(first[key], score=scores[key] / best) for key in order]


class MergedRetriever:
    """Searches several retrievers with the same query and filters, then merges by RRF."""

    def __init__(self, retrievers: Sequence[Retriever]) -> None:
        """Search all of ``retrievers`` (one per collection)."""
        self._retrievers = list(retrievers)

    def search(
        self,
        query: str,
        k: int,
        *,
        extra_codes: Sequence[str] = (),
        filters: _Filters | None = None,
    ) -> list[RetrievedChunk]:
        """``k`` results from each retriever, merged into the best ``k``."""
        lists = [
            r.search(query, k, extra_codes=extra_codes, filters=filters) for r in self._retrievers
        ]
        return merge_rrf(lists, k)


@dataclass(frozen=True)
class Detection:
    """Chunks a customer may see, and the internal ones the detector removed."""

    kept: list[RetrievedChunk]
    dropped: list[RetrievedChunk]


def drop_internal(chunks: Sequence[RetrievedChunk], user: User) -> Detection:
    """For a customer, remove internal chunks and log a SECURITY event for each.

    Agents keep everything. This must never fire in a stage with access control: the
    customer key can't read internal collections. It is a detector, not the control.
    """
    if user.role != "customer":
        return Detection(list(chunks), [])
    dropped = [c for c in chunks if c.audience == INTERNAL]
    for chunk in dropped:
        logger.error(
            "SECURITY: internal chunk %s#%d reached customer %s; dropped",
            chunk.doc_id,
            chunk.chunk_index,
            user.user_id,
        )
    return Detection([c for c in chunks if c.audience != INTERNAL], dropped)


class RoleRetrievers:
    """One retriever per role, each on its own Weaviate connection with that role's key.

    Connections open lazily, the first time a role is needed, so a run with only
    customers never opens the agent connection (or reads the agent key).
    """

    def __init__(
        self,
        stage: StageConfig,
        settings: Settings,
        embedder: Embedder,
        manifest_path: Path,
        opener: ClientOpener | None = None,
    ) -> None:
        """Build retrievers for ``stage`` (``access_control: true``) on demand."""
        if not stage.access_control:
            raise ValueError(f"stage {stage.name} has access_control: false")
        self._stage = stage
        self._settings = settings
        self._embedder = embedder
        self._manifest_path = manifest_path
        self._opener = opener or (lambda key: open_client(settings.weaviate, key))
        self._clients: dict[str, weaviate.WeaviateClient] = {}
        self._retrievers: dict[str, Retriever] = {}

    def for_user(self, user: User | None) -> Retriever:
        """The retriever for ``user``'s role (opening its connection the first time)."""
        if user is None:
            raise ValueError(f"stage {self._stage.name} has access control, so it needs a user")
        if user.role not in self._retrievers:
            self._retrievers[user.role] = self._build(user.role)
        return self._retrievers[user.role]

    def _build(self, role: Role) -> Retriever:
        """Open ``role``'s connection and build one retriever per collection it may read."""
        client = self._opener(key_for_role(role, self._settings))
        self._clients[role] = client
        names = self._stage.audience_collections
        per_collection = []
        for audience in ROLE_AUDIENCES[role]:
            name = names[audience]
            if not client.collections.exists(name):
                raise RuntimeError(
                    f"Collection {name} not found for role {role}. Run: "
                    f"python scripts/ingest.py --stage {self._stage.name}, then "
                    "python scripts/setup_access.py"
                )
            per_collection.append(
                make_retriever(
                    self._stage,
                    client.collections.get(name),
                    self._embedder,
                    self._manifest_path,
                )
            )
        return per_collection[0] if len(per_collection) == 1 else MergedRetriever(per_collection)

    def close(self) -> None:
        """Close every connection that was opened."""
        for client in self._clients.values():
            client.close()
        self._clients.clear()
        self._retrievers.clear()

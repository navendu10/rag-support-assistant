"""Vector-only retrieval (v0): embed the raw message and run ``near_vector``.

Chunk metadata comes back with each result so the evaluator can inspect it, but
it is never used to filter or re-rank results in v0.
"""

from __future__ import annotations

from dataclasses import dataclass

from weaviate.classes.query import MetadataQuery
from weaviate.collections import Collection

from rag_assistant.embeddings import Embedder


@dataclass(frozen=True)
class RetrievedChunk:
    """A chunk returned by search, with its similarity score (1 - cosine distance)."""

    text: str
    doc_id: str
    title: str
    chunk_index: int
    audience: str
    region: str
    score: float


class VectorRetriever:
    """Nearest-neighbour search over one Weaviate collection."""

    def __init__(self, collection: Collection, embedder: Embedder) -> None:
        """Search ``collection`` using vectors from ``embedder``."""
        self._collection = collection
        self._embedder = embedder

    def search(self, query: str, k: int) -> list[RetrievedChunk]:
        """Return the ``k`` chunks closest to ``query``, best first."""
        response = self._collection.query.near_vector(
            near_vector=self._embedder.embed_one(query),
            limit=k,
            return_metadata=MetadataQuery(distance=True),
        )
        return [_to_chunk(obj.properties, obj.metadata.distance) for obj in response.objects]


def _to_chunk(props: dict, distance: float | None) -> RetrievedChunk:
    """Convert a Weaviate object into a ``RetrievedChunk``."""
    return RetrievedChunk(
        text=str(props["text"]),
        doc_id=str(props["doc_id"]),
        title=str(props["title"]),
        chunk_index=int(props["chunk_index"]),
        audience=str(props["audience"]),
        region=str(props["region"]),
        score=1.0 - (distance if distance is not None else 1.0),
    )

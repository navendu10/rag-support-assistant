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
    """A chunk returned by search with its score, higher is better.

    Vector search scores 1 - cosine distance; hybrid search uses Weaviate's fused score.
    Reranked chunks also carry the cross-encoder's raw logit and its sigmoid (0-1).
    """

    text: str
    doc_id: str
    title: str
    chunk_index: int
    audience: str
    region: str
    score: float
    section: str = ""
    page: int | None = None
    rerank_logit: float | None = None
    rerank_score: float | None = None


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
        return [
            to_chunk(obj.properties, 1.0 - _or_one(obj.metadata.distance))
            for obj in response.objects
        ]


def _or_one(distance: float | None) -> float:
    """Treat a missing distance as the worst cosine distance (1.0)."""
    return 1.0 if distance is None else distance


def to_chunk(props: dict, score: float) -> RetrievedChunk:
    """Convert a Weaviate object's properties and score into a ``RetrievedChunk``."""
    return RetrievedChunk(
        text=str(props["text"]),
        doc_id=str(props["doc_id"]),
        title=str(props["title"]),
        chunk_index=int(props["chunk_index"]),
        audience=str(props["audience"]),
        region=str(props["region"]),
        score=score,
        section=str(props.get("section") or ""),
        page=int(props["page"]) if props.get("page") is not None else None,
    )

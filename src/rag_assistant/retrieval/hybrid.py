"""Hybrid retrieval (v2): BM25 keyword search fused with vector search in Weaviate.

The collection has no vectorizer module, so the FastEmbed query vector is passed
explicitly; Weaviate runs BM25 over ``query_properties`` itself and fuses the two
result lists. ``alpha`` weights them (1.0 = pure vector, 0.0 = pure keyword).

With ``alpha_strategy: dynamic`` the weight depends on the question: questions that
contain a product or error code get ``code_alpha`` (more keyword weight), others get
``default_alpha``. The keyword text has codes canonicalised ("xr2200" -> "XR-2200");
the vector is always embedded from the original question.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from weaviate.classes.query import HybridFusion, MetadataQuery
from weaviate.collections import Collection

from rag_assistant.config import HybridConfig
from rag_assistant.embeddings import Embedder
from rag_assistant.retrieval.codes import CodeDetector
from rag_assistant.retrieval.vector import RetrievedChunk, to_chunk

FUSION = {"relative_score": HybridFusion.RELATIVE_SCORE, "ranked": HybridFusion.RANKED}


@dataclass(frozen=True)
class HybridQuery:
    """What one hybrid search sends to Weaviate, apart from the vector."""

    keywords: str
    alpha: float
    codes: tuple[str, ...]


def choose_alpha(cfg: HybridConfig, codes: Sequence[str]) -> float:
    """The fixed alpha, or for the dynamic strategy code_alpha / default_alpha."""
    if cfg.alpha_strategy == "fixed":
        return float(cfg.alpha)  # validated to be set for the fixed strategy
    return float(cfg.code_alpha if codes else cfg.default_alpha)


def plan_query(question: str, cfg: HybridConfig, detector: CodeDetector) -> HybridQuery:
    """Detect codes, pick alpha and build the keyword text for ``question``."""
    codes = tuple(detector.find(question))
    return HybridQuery(detector.canonicalise(question), choose_alpha(cfg, codes), codes)


def hybrid_search(
    collection: Collection,
    keywords: str,
    vector: list[float],
    alpha: float,
    fusion: str,
    query_properties: Sequence[str],
    k: int,
) -> list[RetrievedChunk]:
    """One Weaviate hybrid query with an explicit vector; best ``k`` chunks first."""
    response = collection.query.hybrid(
        query=keywords,
        vector=vector,
        alpha=alpha,
        fusion_type=FUSION[fusion],
        query_properties=list(query_properties),
        limit=k,
        return_metadata=MetadataQuery(score=True),
    )
    return [to_chunk(obj.properties, obj.metadata.score or 0.0) for obj in response.objects]


class HybridRetriever:
    """Hybrid search over one Weaviate collection, configured by a ``HybridConfig``."""

    def __init__(
        self,
        collection: Collection,
        embedder: Embedder,
        cfg: HybridConfig,
        detector: CodeDetector,
    ) -> None:
        """Search ``collection`` with vectors from ``embedder`` and codes from ``detector``."""
        self._collection = collection
        self._embedder = embedder
        self._cfg = cfg
        self._detector = detector

    def search(self, query: str, k: int) -> list[RetrievedChunk]:
        """Return the ``k`` best chunks for ``query``, best first."""
        plan = plan_query(query, self._cfg, self._detector)
        return hybrid_search(
            self._collection,
            plan.keywords,
            self._embedder.embed_one(query),
            plan.alpha,
            self._cfg.fusion,
            self._cfg.query_properties,
            k,
        )

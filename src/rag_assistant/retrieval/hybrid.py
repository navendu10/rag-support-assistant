"""Hybrid retrieval (v2): BM25 keyword search fused with vector search in Weaviate.

The collection has no vectorizer module, so the FastEmbed query vector is passed
explicitly; Weaviate runs BM25 over ``query_properties`` itself and fuses the two
result lists. ``alpha`` weights them (1.0 = pure vector, 0.0 = pure keyword).

With ``alpha_strategy: dynamic`` the weight depends on the question: questions that
contain a product or error code get ``code_alpha`` (more keyword weight), others get
``default_alpha``. The keyword text has codes canonicalised ("xr2200" -> "XR-2200");
the vector is always embedded from the query text as given (with query rewriting,
that is the rewriter's search query).

Session 6 adds two optional inputs: ``extra_codes`` (product codes the query rewriter
extracted, which also count as "the question contains a code") and ``filters`` (the
region filter), which Weaviate applies before ranking.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from weaviate.classes.query import HybridFusion, MetadataQuery
from weaviate.collections import Collection
from weaviate.collections.classes.filters import _Filters

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


def plan_query(
    question: str,
    cfg: HybridConfig,
    detector: CodeDetector,
    extra_codes: Sequence[str] = (),
) -> HybridQuery:
    """Detect codes, pick alpha and build the keyword text for ``question``.

    ``extra_codes`` (from the query rewriter) are merged with the detected codes, so a
    code the rewriter extracted still selects ``code_alpha``.
    """
    found = detector.find(question) + [c.upper() for c in extra_codes]
    codes = tuple(dict.fromkeys(found))
    return HybridQuery(detector.canonicalise(question), choose_alpha(cfg, codes), codes)


def hybrid_search(
    collection: Collection,
    keywords: str,
    vector: list[float],
    alpha: float,
    fusion: str,
    query_properties: Sequence[str],
    k: int,
    filters: _Filters | None = None,
) -> list[RetrievedChunk]:
    """One Weaviate hybrid query with an explicit vector; best ``k`` chunks first.

    ``filters`` restricts both the keyword and the vector side before fusion and ranking.
    """
    response = collection.query.hybrid(
        query=keywords,
        vector=vector,
        alpha=alpha,
        fusion_type=FUSION[fusion],
        query_properties=list(query_properties),
        limit=k,
        filters=filters,
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

    def search(
        self,
        query: str,
        k: int,
        *,
        extra_codes: Sequence[str] = (),
        filters: _Filters | None = None,
    ) -> list[RetrievedChunk]:
        """Return the ``k`` best chunks for ``query``, best first.

        Args:
            query: the text to search for (BM25 and the query vector).
            k: how many chunks to return.
            extra_codes: product codes found by the query rewriter (for dynamic alpha).
            filters: a Weaviate filter applied before ranking (the region filter).
        """
        plan = plan_query(query, self._cfg, self._detector, extra_codes)
        return hybrid_search(
            self._collection,
            plan.keywords,
            self._embedder.embed_one(query),
            plan.alpha,
            self._cfg.fusion,
            self._cfg.query_properties,
            k,
            filters,
        )

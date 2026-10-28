"""Cross-encoder reranking (v3): rescore the search candidates, best first.

Search (vector or hybrid) is a bi-encoder: the question and each chunk are embedded
separately and compared, which is fast but only measures similarity. A cross-encoder
reads the question and a chunk *together* and scores how well the chunk answers it.
That is too slow for the whole corpus, so the pipeline fetches ``fetch_k`` candidates
with search and lets the cross-encoder reorder them.

Replacing the search order with the rerank order outright (``rerank_fusion: replace``)
lost three answers that search had in its top 5, so v3 fuses the two
(``rerank_fusion: blend``); see docs/failures/03-reranking.md.

FastEmbed's ``TextCrossEncoder.rerank`` returns raw logits (about -11 to +10 for
ms-marco-MiniLM), so every chunk also gets ``sigmoid(logit)``: a 0-1 score that makes
thresholds such as Session 8's ``min_score`` readable.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import replace
from functools import cached_property

from fastembed.rerank.cross_encoder import TextCrossEncoder

from rag_assistant.config import RerankFusion
from rag_assistant.retrieval.vector import RetrievedChunk

# (query, documents) -> one raw score per document, in document order.
Scorer = Callable[[str, list[str]], list[float]]


def sigmoid(logit: float) -> float:
    """Map a raw cross-encoder logit to a 0-1 score (overflow-safe)."""
    if logit >= 0:
        return 1.0 / (1.0 + math.exp(-logit))
    z = math.exp(logit)
    return z / (1.0 + z)


def rerank_chunks(
    query: str, chunks: Sequence[RetrievedChunk], scorer: Scorer
) -> list[RetrievedChunk]:
    """``chunks`` reordered by ``scorer``, best first, each with its logit and 0-1 score.

    The sort is stable, so equally scored chunks keep their search order.
    """
    if not chunks:
        return []
    logits = scorer(query, [c.text for c in chunks])
    if len(logits) != len(chunks):
        raise ValueError(f"scorer returned {len(logits)} scores for {len(chunks)} chunks")
    scored = [
        replace(c, rerank_logit=float(logit), rerank_score=sigmoid(float(logit)))
        for c, logit in zip(chunks, logits, strict=True)
    ]
    return sorted(scored, key=lambda c: c.rerank_logit or 0.0, reverse=True)


RRF_K = 60  # the usual reciprocal-rank-fusion constant; not tuned


def _min_max(values: Sequence[float]) -> list[float]:
    """Scale ``values`` to 0-1 (all 1.0 when they are equal)."""
    low, high = min(values), max(values)
    if high == low:
        return [1.0 for _ in values]
    return [(v - low) / (high - low) for v in values]


def fusion_scores(
    search_scores: Sequence[float],
    logits: Sequence[float],
    fusion: RerankFusion,
    weight: float = 0.5,
    rrf_k: int = RRF_K,
) -> list[float]:
    """One ordering score per candidate (higher is better), candidates in search order.

    - ``replace``: the rerank logit alone (search order only breaks ties).
    - ``rrf``: ``1/(rrf_k + search rank) + 1/(rrf_k + rerank rank)``.
    - ``blend``: ``weight * sigmoid(logit) + (1 - weight) * min-max(search score)``.
    """
    if len(search_scores) != len(logits):
        raise ValueError("search_scores and logits must have the same length")
    if not logits:
        return []
    if fusion == "replace":
        return [float(x) for x in logits]
    if fusion == "rrf":
        by_rerank = sorted(range(len(logits)), key=lambda i: -logits[i])
        rerank_rank = {i: r for r, i in enumerate(by_rerank, start=1)}
        return [
            1.0 / (rrf_k + n) + 1.0 / (rrf_k + rerank_rank[i])
            for i, n in enumerate(range(1, len(logits) + 1))
        ]
    search = _min_max(search_scores)
    return [weight * sigmoid(x) + (1.0 - weight) * s for x, s in zip(logits, search, strict=True)]


def fused_order(
    search_scores: Sequence[float],
    logits: Sequence[float],
    fusion: RerankFusion,
    weight: float = 0.5,
    rrf_k: int = RRF_K,
) -> list[int]:
    """Candidate indices, best first, by ``fusion_scores`` (stable: ties keep search order)."""
    scores = fusion_scores(search_scores, logits, fusion, weight, rrf_k)
    return sorted(range(len(scores)), key=lambda i: -scores[i])


def fuse_chunks(
    query: str,
    chunks: Sequence[RetrievedChunk],
    scorer: Scorer,
    fusion: RerankFusion,
    weight: float = 0.5,
) -> list[RetrievedChunk]:
    """Score ``chunks`` (in search order) with ``scorer`` and order them by ``fusion``."""
    if fusion == "replace":
        return rerank_chunks(query, chunks, scorer)
    if not chunks:
        return []
    logits = scorer(query, [c.text for c in chunks])
    if len(logits) != len(chunks):
        raise ValueError(f"scorer returned {len(logits)} scores for {len(chunks)} chunks")
    order = fused_order([c.score for c in chunks], logits, fusion, weight)
    return [
        replace(
            chunks[i],
            rerank_logit=float(logits[i]),
            rerank_score=sigmoid(float(logits[i])),
        )
        for i in order
    ]


class Reranker:
    """A FastEmbed cross-encoder, loaded once on first use and reused."""

    def __init__(self, model_name: str, scorer: Scorer | None = None) -> None:
        """Use ``model_name``, or an injected ``scorer`` (tests) instead of the model."""
        self.model_name = model_name
        self._scorer = scorer

    @cached_property
    def _model(self) -> TextCrossEncoder:
        """The cross-encoder model (downloads on the very first run)."""
        return TextCrossEncoder(model_name=self.model_name)

    def score(self, query: str, documents: list[str]) -> list[float]:
        """Raw logits for each document, in input order."""
        if self._scorer is not None:
            return self._scorer(query, documents)
        return [float(s) for s in self._model.rerank(query, documents)]

    def warm_up(self) -> None:
        """Load the model and run one tiny query, so later timings exclude start-up."""
        self.score("warm up", ["warm up"])

    def rerank(
        self,
        query: str,
        chunks: Sequence[RetrievedChunk],
        fusion: RerankFusion = "replace",
        weight: float = 0.5,
    ) -> list[RetrievedChunk]:
        """``chunks`` (in search order) reordered by ``fusion``, best first.

        Every chunk keeps its search ``score`` and gets the raw ``rerank_logit`` and the
        0-1 ``rerank_score``, whatever the fusion, so Session 8 can threshold on them.
        """
        return fuse_chunks(query, chunks, self.score, fusion, weight)

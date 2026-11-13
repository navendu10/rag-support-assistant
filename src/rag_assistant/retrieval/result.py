"""What one retrieval returns: ranked chunks, their candidates, timings and scores."""

from __future__ import annotations

from dataclasses import dataclass

from rag_assistant.retrieval.vector import RetrievedChunk


@dataclass(frozen=True)
class Retrieval:
    """Ranked chunks, the search candidates they came from, and how long each step took.

    ``security_events`` counts internal chunks the detector removed for a customer
    (access-control stages only); it should always be 0. ``best_rerank_score`` is the
    highest cross-encoder score (sigmoid, 0-1) among **all** reranked candidates, before
    the cut to ``k``; None without reranking or candidates. The corrective flow grades on
    it (Session 8).
    """

    ranked: list[RetrievedChunk]
    candidates: list[RetrievedChunk]
    timings_ms: dict[str, float]
    security_events: int = 0
    best_rerank_score: float | None = None

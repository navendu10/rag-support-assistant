"""A deterministic order for retrieved chunks (Session 13).

Equal scores used to keep whatever order Weaviate returned, which can change when a
collection is re-ingested: identical HTML and print twins (``returns-uk-2026#1`` and
``returns-uk-2026-print#1``) swapped places in 13 golden questions after the Session 11
re-ingest. Every retrieval path now orders by **score, then the document's effective date
(newest first), then doc_id, then chunk index**, so a tie never prefers a superseded
document (the 2025 UK returns policy over its 2026 replacement):

- vector and hybrid search (``order_chunks`` on Weaviate's results);
- the reranker's sort and the fused order (``rank_key`` with the fused score);
- the RRF merge across collections (``access.merge_rrf``).

**The cut-off.** Weaviate itself decides which chunks make a ``limit`` when scores tie at
the boundary. So every search asks for ``TIE_MARGIN`` extra chunks, orders them here, and
only then cuts to ``k`` (``search_limit``). The margin is not a tuning knob: it only makes
the boundary deterministic.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # vector.py imports this module
    from rag_assistant.retrieval.vector import RetrievedChunk

TIE_MARGIN = 5  # extra results fetched so ties at the cut-off are broken here, not by Weaviate


def date_rank(effective_date: str) -> int:
    """The date's ordinal; 0 for a missing or unreadable date (it sorts as the oldest)."""
    try:
        return date.fromisoformat(effective_date[:10]).toordinal()
    except ValueError:
        return 0


def rank_key(score: float, chunk: RetrievedChunk) -> tuple[float, int, str, int]:
    """Best first: a higher ``score``, then a newer effective date, then ``doc_id`` and
    ``chunk_index`` (ascending)."""
    return (-score, -date_rank(chunk.effective_date), chunk.doc_id, chunk.chunk_index)


def order_chunks(chunks: Sequence[RetrievedChunk], k: int | None = None) -> list[RetrievedChunk]:
    """``chunks`` by search score with the deterministic tie-break, cut to ``k``."""
    ordered = sorted(chunks, key=lambda c: rank_key(c.score, c))
    return ordered if k is None else ordered[:k]


def search_limit(k: int) -> int:
    """How many results to ask Weaviate for, to return a deterministic best ``k``."""
    return k + TIE_MARGIN

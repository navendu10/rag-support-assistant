"""The deterministic tie-break (Session 13): equal scores are ordered by doc_id, then chunk
index, in every retrieval path, and the cut-off is applied after that order."""

from __future__ import annotations

import itertools
import random
from types import SimpleNamespace

from rag_assistant.retrieval.access import merge_rrf
from rag_assistant.retrieval.hybrid import hybrid_search
from rag_assistant.retrieval.ordering import TIE_MARGIN, order_chunks, rank_key, search_limit
from rag_assistant.retrieval.rerank import fuse_chunks
from rag_assistant.retrieval.vector import RetrievedChunk, VectorRetriever


def _chunk(doc_id: str, index: int, score: float) -> RetrievedChunk:
    """A chunk with a given search score."""
    return RetrievedChunk(f"{doc_id}#{index}", doc_id, doc_id, index, "public", "ALL", score)


TWINS = [
    _chunk("returns-uk-2026-print", 1, 0.86),
    _chunk("returns-uk-2026", 1, 0.86),
    _chunk("returns-uk-2025-print", 1, 0.86),
    _chunk("faq-030", 0, 1.0),
    _chunk("returns-uk-2026", 2, 0.56),
]


def _ids(chunks: list[RetrievedChunk]) -> list[str]:
    return [f"{c.doc_id}#{c.chunk_index}" for c in chunks]


def test_order_is_score_then_doc_id_then_chunk_index() -> None:
    """The HTML and print twins (equal scores) come out in doc_id order."""
    assert _ids(order_chunks(TWINS)) == [
        "faq-030#0",
        "returns-uk-2025-print#1",
        "returns-uk-2026#1",
        "returns-uk-2026-print#1",
        "returns-uk-2026#2",
    ]
    assert rank_key(0.5, TWINS[0]) < rank_key(0.4, TWINS[0])


def test_the_order_does_not_depend_on_the_order_weaviate_returned() -> None:
    """Every permutation of the same results gives the same top k."""
    expected = _ids(order_chunks(TWINS, 3))
    for perm in itertools.permutations(TWINS):
        assert _ids(order_chunks(list(perm), 3)) == expected


def _fake_vector_collection(results: list[RetrievedChunk]) -> SimpleNamespace:
    """A collection whose near_vector returns ``results`` (shuffled), up to ``limit``."""
    calls: list[int] = []

    def near_vector(near_vector: list[float], limit: int, **_: object) -> SimpleNamespace:
        calls.append(limit)
        shuffled = random.Random(limit).sample(results, len(results))[:limit]
        return SimpleNamespace(
            objects=[
                SimpleNamespace(
                    properties={
                        "text": c.text,
                        "doc_id": c.doc_id,
                        "title": c.title,
                        "chunk_index": c.chunk_index,
                        "audience": "public",
                        "region": "ALL",
                    },  # fmt: skip
                    metadata=SimpleNamespace(distance=1.0 - c.score),
                )
                for c in shuffled
            ]
        )

    return SimpleNamespace(query=SimpleNamespace(near_vector=near_vector), calls=calls)


def test_vector_search_asks_for_the_margin_and_cuts_after_ordering() -> None:
    """A tie at the k boundary is broken by doc_id, not by what Weaviate kept."""
    collection = _fake_vector_collection(TWINS)
    embedder = SimpleNamespace(embed_one=lambda text: [0.0])
    found = VectorRetriever(collection, embedder).search("q", 2)  # type: ignore[arg-type]
    assert collection.calls == [2 + TIE_MARGIN] and search_limit(2) == 2 + TIE_MARGIN
    assert _ids(found) == ["faq-030#0", "returns-uk-2025-print#1"]


def test_hybrid_search_orders_ties_before_the_cut() -> None:
    """The same for hybrid search."""

    def hybrid(**kwargs: object) -> SimpleNamespace:
        objects = [
            SimpleNamespace(
                properties={
                    "text": c.text,
                    "doc_id": c.doc_id,
                    "title": c.title,
                    "chunk_index": c.chunk_index,
                    "audience": "public",
                    "region": "ALL",
                },  # fmt: skip
                metadata=SimpleNamespace(score=c.score),
            )
            for c in reversed(TWINS)
        ]
        return SimpleNamespace(objects=objects)

    collection = SimpleNamespace(query=SimpleNamespace(hybrid=hybrid))
    found = hybrid_search(collection, "q", [0.0], 0.5, "relative_score", ["text"], 3)  # type: ignore[arg-type]
    assert _ids(found) == ["faq-030#0", "returns-uk-2025-print#1", "returns-uk-2026#1"]


def test_rrf_merge_and_fused_rerank_break_ties_the_same_way() -> None:
    """Equal fused scores across collections and after reranking: doc_id, chunk index."""
    merged = merge_rrf([[_chunk("pub-b", 0, 1.0)], [_chunk("int-a", 0, 1.0)]], 2)
    assert _ids(merged) == ["int-a#0", "pub-b#0"]
    flat = fuse_chunks("q", [TWINS[0], TWINS[1], TWINS[2]], lambda q, d: [0.0] * len(d), "blend")
    assert _ids(flat) == ["returns-uk-2025-print#1", "returns-uk-2026#1", "returns-uk-2026-print#1"]


def test_a_tie_prefers_the_newest_document_over_a_superseded_one() -> None:
    """The 2025 UK policy and its 2026 replacement tie: the 2026 one comes first, in
    search order, after reranking and in the RRF merge; doc_id only breaks same-date ties."""
    from dataclasses import replace

    stale = replace(_chunk("returns-uk-2025-print", 1, 0.86), effective_date="2025-01-01")
    html = replace(_chunk("returns-uk-2026", 1, 0.86), effective_date="2026-04-01")
    pdf = replace(_chunk("returns-uk-2026-print", 1, 0.86), effective_date="2026-04-01")
    undated = _chunk("faq-001", 0, 0.86)
    expected = ["returns-uk-2026#1", "returns-uk-2026-print#1", "returns-uk-2025-print#1",
                "faq-001#0"]  # fmt: skip
    for perm in itertools.permutations([stale, html, pdf, undated]):
        assert _ids(order_chunks(list(perm))) == expected
    flat = fuse_chunks("q", [stale, pdf, html], lambda q, d: [0.0] * len(d), "blend")
    assert _ids(flat) == expected[:3]
    assert _ids(merge_rrf([[stale], [html]], 2)) == ["returns-uk-2026#1", "returns-uk-2025-print#1"]

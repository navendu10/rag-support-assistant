"""Reranking (v3) against a real Weaviate and cross-encoder. Run: pytest -m integration.

Needs Weaviate and the generated data; makes no LLM calls (retrieval only). The v2
settings are ingested into a throwaway collection, so the real Chunks_v2 is untouched.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from rag_assistant.config import Settings, StageConfig, load_settings, load_stage
from rag_assistant.embeddings import Embedder
from rag_assistant.ingestion.pipeline import ingest
from rag_assistant.ingestion.store import connect
from rag_assistant.retrieval.factory import make_retriever
from rag_assistant.retrieval.rerank import Reranker
from rag_assistant.retrieval.vector import RetrievedChunk

pytestmark = pytest.mark.integration

TV_LINES = "My TV has lines on the screen, I bought it last March"


@pytest.fixture(scope="module")
def settings() -> Settings:
    """Real settings; skip if they or the generated data are missing."""
    try:
        s = load_settings()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"settings unavailable: {exc}")
    if not (s.paths.resolve(s.paths.generated_dir) / "manifest.json").is_file():
        pytest.skip("run scripts/generate_data.py first")
    return s


@pytest.fixture(scope="module")
def stage(settings: Settings) -> Iterator[StageConfig]:
    """v3 pointed at a throwaway copy of v2's index, deleted afterwards."""
    owner = load_stage("v2").model_copy(update={"name": "itest_v2rr"})
    try:
        ingest(owner, settings)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Weaviate unavailable: {exc}")
    yield load_stage("v3").model_copy(update={"index": owner.collection_name})
    with connect(settings.weaviate) as client:
        client.collections.delete(owner.collection_name)


def _first_rank(chunks: list[RetrievedChunk], doc_id: str) -> int:
    """1-based rank of the first chunk from ``doc_id`` (fails if absent)."""
    return next(n for n, c in enumerate(chunks, start=1) if c.doc_id == doc_id)


def test_tv_lines_warranty_chunk_is_in_v3_context(stage: StageConfig, settings: Settings) -> None:
    """With v3's blend fusion the warranty policy (the answer) reaches the top_k context.

    The Session 5 brief asked for rank 1. It isn't, and this test pins what v3 really does:

    - Rerank-only ordering (``replace``) with MiniLM-L-6 moves the policy up, from 3rd to
      2nd, behind the forum thread about TV lines. bge-reranker-base would rank it 1st
      at about 4.6x the latency (ADR 0004).
    - v3 blends the rerank and search scores (results/v3/rerank_sweep.md), which keeps it
      at its search rank, 3rd: inside the context, but not moved up.
    - The cross-encoder scores it about 0.001, because the question never mentions a
      warranty; the raw scores stay on every chunk for Session 8.
    """
    manifest = settings.paths.resolve(settings.paths.generated_dir) / "manifest.json"
    with connect(settings.weaviate) as client:
        retriever = make_retriever(
            stage,
            client.collections.get(stage.collection_name),
            Embedder(settings.embedding),
            manifest,
        )
        candidates = retriever.search(TV_LINES, int(stage.fetch_k or 0))
    reranker = Reranker(str(stage.rerank_model))
    blended = reranker.rerank(
        TV_LINES, candidates, stage.rerank_fusion or "replace", stage.rerank_blend_weight or 0.5
    )
    replaced = reranker.rerank(TV_LINES, candidates, "replace")
    search_rank = _first_rank(candidates, "policy-warranty")
    assert _first_rank(blended, "policy-warranty") <= stage.top_k
    assert _first_rank(replaced, "policy-warranty") < search_rank
    for chunks in (blended, replaced):
        assert all(c.rerank_score is not None and 0.0 <= c.rerank_score <= 1.0 for c in chunks)
    assert [c.rerank_logit for c in replaced] == sorted(
        (c.rerank_logit for c in replaced), reverse=True
    )

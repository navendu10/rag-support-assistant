"""Hybrid search (v2) against a real Weaviate. Run with: pytest -m integration.

Needs Weaviate and the generated data; makes no LLM calls (retrieval only).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from rag_assistant.config import Settings, StageConfig, load_settings, load_stage
from rag_assistant.embeddings import Embedder
from rag_assistant.ingestion.pipeline import ingest
from rag_assistant.ingestion.store import connect
from rag_assistant.retrieval.factory import make_retriever
from rag_assistant.retrieval.hybrid import HybridRetriever
from rag_assistant.retrieval.vector import RetrievedChunk

pytestmark = pytest.mark.integration


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
    """v2 settings ingested into a throwaway collection, deleted afterwards."""
    stage = load_stage("v2").model_copy(update={"name": "itest_v2"})
    try:
        ingest(stage, settings)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Weaviate unavailable: {exc}")
    yield stage
    with connect(settings.weaviate) as client:
        client.collections.delete(stage.collection_name)


def _search(stage: StageConfig, settings: Settings, question: str) -> list[RetrievedChunk]:
    """Top 5 chunks for ``question`` with the stage's retriever."""
    manifest = settings.paths.resolve(settings.paths.generated_dir) / "manifest.json"
    with connect(settings.weaviate) as client:
        retriever = make_retriever(
            stage,
            client.collections.get(stage.collection_name),
            Embedder(settings.embedding),
            manifest,
        )
        assert isinstance(retriever, HybridRetriever)
        return retriever.search(question, k=5)


def test_xr_2200_reset_ranks_its_manual_first(stage: StageConfig, settings: Settings) -> None:
    """The XR-2200 manual ranks first, and the XR-2000 manual stays out of the context."""
    chunks = _search(stage, settings, "How do I factory reset the XR-2200?")
    assert chunks[0].doc_id == "manual-xr-2200"
    assert "Factory reset" in chunks[0].section
    assert "manual-xr-2000" not in [c.doc_id for c in chunks[: stage.top_k]]
    assert [c.score for c in chunks] == sorted((c.score for c in chunks), reverse=True)


def test_code_without_hyphen_still_matches(stage: StageConfig, settings: Settings) -> None:
    """'xr2200' is canonicalised for keyword search, so the right manual still wins."""
    chunks = _search(stage, settings, "how do i factory reset the xr2200")
    assert chunks[0].doc_id == "manual-xr-2200"

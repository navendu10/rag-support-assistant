"""End-to-end checks against real Weaviate and Groq. Run with: pytest -m integration."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from rag_assistant.config import Settings, StageConfig, load_prompts, load_settings, load_stage
from rag_assistant.eval.judge import Judge
from rag_assistant.ingestion.pipeline import ingest
from rag_assistant.ingestion.store import connect
from rag_assistant.llm import make_chat_model
from rag_assistant.pipeline import Pipeline
from rag_assistant.schemas import GoldenItem

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def settings() -> Settings:
    """Real settings; skip if the Groq key or generated data is missing."""
    try:
        s = load_settings()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"settings unavailable: {exc}")
    if not (s.paths.resolve(s.paths.generated_dir) / "manifest.json").is_file():
        pytest.skip("run scripts/generate_data.py first")
    return s


@pytest.fixture(scope="module")
def stage(settings: Settings) -> Iterator[StageConfig]:
    """v0 settings ingested into a throwaway collection, deleted afterwards."""
    stage = load_stage("v0").model_copy(update={"name": "itest"})
    try:
        stats = ingest(stage, settings)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Weaviate unavailable: {exc}")
    assert stats.chunks > 100
    assert stats.chunks_by_audience["internal"] > 0
    yield stage
    with connect(settings.weaviate) as client:
        client.collections.delete(stage.collection_name)


def test_retrieval_returns_metadata_and_scores(stage: StageConfig, settings: Settings) -> None:
    """near_vector returns k chunks, best first, with evaluation metadata."""
    with Pipeline.from_stage(stage, settings) as pipeline:
        chunks = pipeline.retrieve("How do I factory reset the XR-2200?", k=5)
    assert len(chunks) == 5
    assert [c.score for c in chunks] == sorted((c.score for c in chunks), reverse=True)
    assert all(c.doc_id and c.audience in {"public", "internal"} for c in chunks)


def test_answer_and_judge(stage: StageConfig, settings: Settings) -> None:
    """A full answer plus one correctness judgement."""
    question = "How long does the AeroBuds Pro battery last?"
    with Pipeline.from_stage(stage, settings) as pipeline:
        answer = pipeline.answer(question, retrieve_k=5)
    assert answer.text
    assert len(answer.context) == stage.top_k and len(answer.retrieved) == 5
    item = GoldenItem(
        id="g999",
        question=question,
        user_id="cust_uk_01",
        failure_tag="normal",
        topic="t",
        expected_doc_ids=["manual-ab-pro"],
        reference_answer="Up to 8 hours, or 24 hours with the charging case.",
        expect_handoff=False,
        must_not_contain=[],
    )
    judge = Judge(make_chat_model(settings, settings.llm.judge_model), load_prompts())
    assert judge.correctness(item, answer.text).verdict in {"CORRECT", "INCORRECT"}

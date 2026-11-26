"""RAGAS scoring (Session 13 Part B) with a scripted model: no network, and never OpenAI."""

from __future__ import annotations

import json

import openai
import pytest
from langchain_core.embeddings import DeterministicFakeEmbedding
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from rag_assistant.eval.ragas_eval import RagasRow, make_scorers, mean, score_row, summarise

CONTEXT = "Opened audio products can be returned within 15 days of delivery."
STATEMENT = "Opened earbuds can be returned within 15 days."


@pytest.fixture(autouse=True)
def no_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail the test if anything tries to build an OpenAI client (and set no key)."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("an OpenAI client was created")

    monkeypatch.setattr(openai.OpenAI, "__init__", refuse)
    monkeypatch.setattr(openai.AsyncOpenAI, "__init__", refuse)


def _scripted() -> FakeListChatModel:
    """Replies in the order RAGAS asks: statements, verdicts, then the generated question."""
    return FakeListChatModel(
        responses=[
            json.dumps({"statements": [STATEMENT]}),
            json.dumps(
                {"statements": [{"statement": STATEMENT, "reason": "in the context", "verdict": 1}]}
            ),  # fmt: skip
            json.dumps({"question": "Can I return opened earbuds?", "noncommittal": 0}),
        ]
    )


def test_all_four_metrics_score_without_openai() -> None:
    """Faithfulness and answer relevancy use the given model; context precision and recall
    need no model; nothing creates an OpenAI client."""
    scorers = make_scorers(_scripted(), DeterministicFakeEmbedding(size=16))
    row = score_row(scorers, "g001", "chunking", "Can I return opened earbuds?", STATEMENT,
                    [CONTEXT], [CONTEXT])  # fmt: skip
    assert row.faithfulness == 1.0
    assert row.answer_relevancy is not None
    assert row.context_precision == 1.0 and row.context_recall == 1.0


def test_rows_without_context_or_references_get_no_context_metrics() -> None:
    """No context: no faithfulness or context metrics; no expected docs: no precision or
    recall. Answer relevancy still applies."""
    scorers = make_scorers(
        FakeListChatModel(responses=[json.dumps({"question": "q?", "noncommittal": 0})] * 4),
        DeterministicFakeEmbedding(size=16),
    )
    bare = score_row(scorers, "g022", "hallucination", "q?", "I can't find that.", [], [])
    assert (bare.faithfulness, bare.context_precision, bare.context_recall) == (None, None, None)
    assert bare.answer_relevancy is not None


def test_means_skip_unscored_rows_and_split_by_tag() -> None:
    """None is "not applicable", never zero."""
    rows = [
        RagasRow("g001", "qwen", "chunking", 1.0, 0.8, 1.0, 0.5),
        RagasRow("g022", "qwen", "hallucination", None, 0.4, None, None),
    ]
    summary = summarise(rows)
    assert summary["all"]["faithfulness"] == 1.0 and summary["all"]["answer_relevancy"] == 0.6
    assert summary["hallucination"]["context_recall"] is None
    assert mean([None, None]) is None


def test_a_quota_error_stops_the_run_instead_of_recording_a_gap() -> None:
    """A spent quota must not be stored as "faithfulness: n/a" (Session 13 found 3 rows
    recorded that way); the error propagates so the row is scored on resume."""
    from rag_assistant.llm import RateLimitExhaustedError

    class SpentQuota(FakeListChatModel):
        def _call(self, *args: object, **kwargs: object) -> str:
            raise RateLimitExhaustedError("Groq asked to wait 369s")

    scorers = make_scorers(SpentQuota(responses=["x"]), DeterministicFakeEmbedding(size=16))
    with pytest.raises(RateLimitExhaustedError):
        score_row(scorers, "g011", "ranking", "q?", STATEMENT, [CONTEXT], [CONTEXT])

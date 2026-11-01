"""Tests for the region filter: built only from the user profile (no network)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from weaviate.collections.classes.filters import _FilterOr, _FilterValue

from rag_assistant.config import HybridConfig, load_stage
from rag_assistant.pipeline import Pipeline
from rag_assistant.query.rewrite import Catalogue, QueryRewriter, Rewrite, RewriteResult
from rag_assistant.retrieval.codes import CodeDetector
from rag_assistant.retrieval.filters import build_region_filter, region_filter, region_values
from rag_assistant.retrieval.hybrid import HybridRetriever, plan_query
from rag_assistant.retrieval.vector import RetrievedChunk
from rag_assistant.schemas import Turn, User

UK = User(user_id="cust_uk_01", name="Oliver", role="customer", region="UK")
IN = User(user_id="cust_in_01", name="Priya", role="customer", region="IN")
AGENT = User(user_id="agent_01", name="Agent", role="agent", region="ALL")


def _values(flt: object) -> list[tuple[str, object]]:
    """(property, value) pairs of an any_of filter."""
    assert isinstance(flt, _FilterOr)
    pairs = []
    for f in flt.filters:
        assert isinstance(f, _FilterValue)
        pairs.append((str(f.target), f.value))
    return pairs


def test_customers_get_their_region_and_all() -> None:
    """A customer searches their own region's documents plus region ALL."""
    assert region_values(UK) == ("UK", "ALL")
    assert region_values(IN) == ("IN", "ALL")
    assert _values(region_filter(UK)) == [("region", "UK"), ("region", "ALL")]


def test_agents_get_no_region_filter() -> None:
    """Agents (region ALL) serve every region, so nothing is filtered."""
    assert region_values(AGENT) is None
    assert region_filter(AGENT) is None
    assert build_region_filter(None) is None


# ---------------------------------------------------------------- pipeline


class RecordingRetriever:
    """Records every search call's query and options."""

    def __init__(self) -> None:
        """No calls yet."""
        self.calls: list[dict] = []

    def search(self, query: str, k: int, **options: object) -> list[RetrievedChunk]:
        """Remember the call and return one chunk."""
        self.calls.append({"query": query, "k": k, **options})
        return [RetrievedChunk("t", "returns-uk-2026", "Returns", 0, "public", "UK", 0.9)]


class EchoGenerator:
    """Returns the question it was asked to answer."""

    def __init__(self) -> None:
        """No questions yet."""
        self.questions: list[str] = []

    def generate(self, question: str, chunks: object) -> str:
        """Echo the question."""
        self.questions.append(question)
        return f"answer to: {question}"


class FixedRewriter(QueryRewriter):
    """Always returns the same rewrite, recording the history it was given."""

    def __init__(self, rewrite: Rewrite) -> None:
        """Rewrite every message to ``rewrite``."""
        super().__init__(lambda _: "", "", "", Catalogue(()))
        self._fixed = rewrite
        self.histories: list[int] = []

    def rewrite(self, message: str, history: object = ()) -> RewriteResult:
        """The fixed rewrite."""
        self.histories.append(len(list(history)))  # type: ignore[arg-type]
        return RewriteResult(self._fixed, fallback=False, attempts=1)


def _pipeline(stage: str, rewrite: Rewrite) -> tuple[Pipeline, RecordingRetriever, EchoGenerator]:
    """A stage's pipeline with fakes: no reranker (rerank switched off), fixed rewriter."""
    cfg = load_stage(stage).model_copy(
        update={
            "rerank": False,
            "rerank_model": None,
            "fetch_k": None,
            "rerank_fusion": None,
            "rerank_blend_weight": None,
        }
    )
    retriever, generator = RecordingRetriever(), EchoGenerator()
    rewriter = FixedRewriter(rewrite) if cfg.query_rewrite else None
    return Pipeline(cfg, retriever, generator, rewriter=rewriter), retriever, generator  # type: ignore[arg-type]


INDIA_CLAIM = "I'm in India, can I return it?"
REWRITE = Rewrite(
    standalone_question="I'm in India, can I return the AeroBuds Pro?",
    search_query="Audio return window India",
    product_codes=["AB-PRO"],
    product_category="Audio",
)


def test_region_in_the_question_never_changes_the_filter() -> None:
    """A UK customer saying "I'm in India" is still filtered to UK + ALL."""
    pipeline, retriever, _ = _pipeline("v4", REWRITE)
    answer = pipeline.answer(INDIA_CLAIM, UK)
    assert answer.region_filter == ("UK", "ALL")
    assert _values(retriever.calls[0]["filters"]) == [("region", "UK"), ("region", "ALL")]


def test_agents_are_not_filtered_in_v4() -> None:
    """agent_01 has region ALL: no filter is sent."""
    pipeline, retriever, _ = _pipeline("v4", REWRITE)
    assert pipeline.answer("Can I return it?", AGENT).region_filter is None
    assert retriever.calls[0]["filters"] is None


def test_v4a_rewrites_but_does_not_filter() -> None:
    """v4a measures rewriting alone."""
    pipeline, retriever, _ = _pipeline("v4a", REWRITE)
    assert pipeline.answer(INDIA_CLAIM, UK).region_filter is None
    assert retriever.calls[0]["filters"] is None


def test_filtering_stage_needs_a_user() -> None:
    """Without a profile there is nothing to filter by."""
    pipeline, _, _ = _pipeline("v4", REWRITE)
    with pytest.raises(ValueError, match="needs a user"):
        pipeline.answer("Can I return it?")


def test_rewrite_routes_search_query_and_standalone_question() -> None:
    """search_query and codes go to search; standalone_question goes to generation."""
    pipeline, retriever, generator = _pipeline("v4", REWRITE)
    answer = pipeline.answer("Can I return it?", UK, [Turn(role="user", content="AeroBuds Pro?")])
    call = retriever.calls[0]
    assert call["query"] == "Audio return window India"
    assert call["extra_codes"] == ("AB-PRO",)
    assert generator.questions == [REWRITE.standalone_question]
    assert answer.rewrite is not None and not answer.rewrite.fallback
    assert {"rewrite", "rewrite_wait", "rewrite_requests"} <= set(answer.timings_ms)


def test_pipeline_needs_a_rewriter_exactly_when_rewriting() -> None:
    """Like the reranker, the rewriter must match the stage switch."""
    stage = load_stage("v2")
    with pytest.raises(ValueError, match="rewriter"):
        Pipeline(stage, RecordingRetriever(), EchoGenerator(), rewriter=FixedRewriter(REWRITE))  # type: ignore[arg-type]


# ------------------------------------------------------------ hybrid query


def test_extracted_codes_select_code_alpha() -> None:
    """A code the rewriter extracted counts as a code question even if the text has none."""
    cfg = HybridConfig(alpha_strategy="dynamic", code_alpha=0.25, default_alpha=1.0)
    detector = CodeDetector.from_codes(["AB-PRO"])
    assert plan_query("Audio return window", cfg, detector).alpha == 1.0
    plan = plan_query("Audio return window", cfg, detector, ["ab-pro"])
    assert (plan.alpha, plan.codes) == (0.25, ("AB-PRO",))


def test_hybrid_retriever_passes_the_filter_to_weaviate() -> None:
    """The filter goes inside the hybrid query, so it applies before ranking."""
    calls: list[dict] = []

    def hybrid(**kwargs: object) -> SimpleNamespace:
        calls.append(kwargs)
        return SimpleNamespace(objects=[])

    collection = SimpleNamespace(query=SimpleNamespace(hybrid=hybrid))
    embedder = SimpleNamespace(embed_one=lambda text: [0.0])
    cfg = HybridConfig(alpha_strategy="fixed", alpha=0.5)
    retriever = HybridRetriever(collection, embedder, cfg, CodeDetector.from_codes([]))  # type: ignore[arg-type]
    flt = region_filter(UK)
    retriever.search("returns", 20, filters=flt)
    assert calls[0]["filters"] is flt and calls[0]["limit"] == 20

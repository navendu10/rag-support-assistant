"""Tests for hybrid search: alpha selection, query planning, config and the alpha sweep."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from weaviate.classes.query import HybridFusion

from rag_assistant.config import KEYWORD_PROPERTIES, HybridConfig, StageConfig, load_stage
from rag_assistant.eval.sweep import (
    context_hits,
    dynamic_matrix,
    dynamic_run,
    question_groups,
    score_group,
    sweep_table,
)
from rag_assistant.ingestion.store import PROPERTIES
from rag_assistant.retrieval.codes import CodeDetector
from rag_assistant.retrieval.factory import make_retriever
from rag_assistant.retrieval.hybrid import HybridRetriever, choose_alpha, plan_query
from rag_assistant.retrieval.vector import VectorRetriever
from rag_assistant.schemas import GoldenItem

DETECTOR = CodeDetector.from_codes(["XR-2200", "XR-2000", "PB-20K"])
DYNAMIC = HybridConfig(alpha_strategy="dynamic", code_alpha=0.25, default_alpha=1.0)


# --------------------------------------------------------------- alpha choice


def test_fixed_alpha_ignores_codes() -> None:
    """The fixed strategy always uses ``alpha``."""
    cfg = HybridConfig(alpha=0.5)
    assert choose_alpha(cfg, []) == choose_alpha(cfg, ["XR-2200"]) == 0.5


def test_dynamic_alpha_depends_on_codes() -> None:
    """Dynamic: code_alpha with a code, default_alpha without."""
    assert choose_alpha(DYNAMIC, ["E-47"]) == 0.25
    assert choose_alpha(DYNAMIC, []) == 1.0


def test_plan_query_canonicalises_keywords_and_picks_alpha() -> None:
    """An unhyphenated code is rewritten for BM25 and switches to code_alpha."""
    plan = plan_query("How do I reset the xr2200?", DYNAMIC, DETECTOR)
    assert (plan.keywords, plan.alpha, plan.codes) == (
        "How do I reset the XR-2200?",
        0.25,
        ("XR-2200",),
    )
    plain = plan_query("Can I return opened headphones?", DYNAMIC, DETECTOR)
    assert (plain.keywords, plain.alpha, plain.codes) == (
        "Can I return opened headphones?",
        1.0,
        (),
    )


# --------------------------------------------------------------- retriever


@dataclass
class FakeQuery:
    """Records the arguments of ``collection.query.hybrid``."""

    calls: list[dict] = field(default_factory=list)

    def hybrid(self, **kwargs: object) -> SimpleNamespace:
        """Return one fake object with a score."""
        self.calls.append(kwargs)
        props = {
            "text": "t",
            "doc_id": "manual-xr-2200",
            "title": "XR-2200",
            "chunk_index": 6,
            "audience": "public",
            "region": "ALL",
            "section": "Factory reset",
            "page": 2,
        }
        return SimpleNamespace(
            objects=[SimpleNamespace(properties=props, metadata=SimpleNamespace(score=0.9))]
        )


class FakeEmbedder:
    """Returns a fixed vector and remembers what it embedded."""

    def __init__(self) -> None:
        """Start with no calls."""
        self.texts: list[str] = []

    def embed_one(self, text: str) -> list[float]:
        """A 3-dimensional dummy vector."""
        self.texts.append(text)
        return [0.1, 0.2, 0.3]


def test_hybrid_retriever_sends_vector_alpha_fusion_and_properties() -> None:
    """The query vector is passed explicitly (no vectorizer) with the planned settings."""
    query, embedder = FakeQuery(), FakeEmbedder()
    collection = SimpleNamespace(query=query)
    cfg = HybridConfig(
        alpha_strategy="dynamic",
        code_alpha=0.25,
        default_alpha=1.0,
        fusion="ranked",
        query_properties=("text", "product_code^2"),
    )
    retriever = HybridRetriever(collection, embedder, cfg, DETECTOR)  # type: ignore[arg-type]
    chunks = retriever.search("reset the xr2200", k=5)
    call = query.calls[0]
    assert call["query"] == "reset the XR-2200"
    assert call["vector"] == [0.1, 0.2, 0.3]
    assert (call["alpha"], call["limit"]) == (0.25, 5)
    assert call["fusion_type"] == HybridFusion.RANKED
    assert call["query_properties"] == ["text", "product_code^2"]
    assert embedder.texts == ["reset the xr2200"]  # the vector uses the original text
    assert (chunks[0].doc_id, chunks[0].score, chunks[0].section) == (
        "manual-xr-2200",
        0.9,
        "Factory reset",
    )


def test_make_retriever_follows_the_stage(generated_dir: Path) -> None:
    """search: vector gives the vector retriever; search: hybrid the hybrid one."""
    manifest = generated_dir / "manifest.json"
    collection, embedder = SimpleNamespace(), FakeEmbedder()
    v1 = make_retriever(load_stage("v1"), collection, embedder, manifest)  # type: ignore[arg-type]
    v2 = make_retriever(load_stage("v2"), collection, embedder, manifest)  # type: ignore[arg-type]
    assert isinstance(v1, VectorRetriever)
    assert isinstance(v2, HybridRetriever)


# ------------------------------------------------------------------ config


@pytest.mark.parametrize(
    "settings",
    [
        {"alpha_strategy": "fixed"},  # alpha missing
        {"alpha_strategy": "fixed", "alpha": 0.5, "code_alpha": 0.2},
        {"alpha_strategy": "dynamic", "code_alpha": 0.2},  # default_alpha missing
        {"alpha_strategy": "dynamic", "code_alpha": 0.2, "default_alpha": 0.8, "alpha": 0.5},
        {"alpha": 1.5},
        {"alpha": 0.5, "fusion": "rrf"},
        {"alpha": 0.5, "query_properties": []},
        {"alpha": 0.5, "query_properties": ["doc_id"]},  # not keyword-searchable
        {"alpha": 0.5, "query_properties": ["text^x"]},
        {"alpha": 0.5, "query_properties": ["text", "text^2"]},
        {"alpha": 0.5, "boost": 2},  # unknown key
    ],
)
def test_invalid_hybrid_settings_rejected(settings: dict) -> None:
    """Alphas must match the strategy; properties must be known, optionally boosted."""
    with pytest.raises(ValidationError):
        HybridConfig.model_validate(settings)


def test_valid_hybrid_settings() -> None:
    """Boosts may be integers or decimals; the defaults are text, title and product_code^2."""
    cfg = HybridConfig(alpha=0.5, query_properties=("text^1.5", "section"))
    assert cfg.query_properties == ("text^1.5", "section")
    assert HybridConfig(alpha=0.5).query_properties == ("text", "title", "product_code^2")


def test_hybrid_block_required_exactly_for_hybrid_search() -> None:
    """search: hybrid needs a hybrid block, and search: vector must not have one."""
    v1 = load_stage("v1").model_dump()
    with pytest.raises(ValidationError):
        StageConfig.model_validate({**v1, "search": "hybrid", "hybrid": None})
    with pytest.raises(ValidationError):
        StageConfig.model_validate({**v1, "hybrid": {"alpha": 0.5}})
    stage = StageConfig.model_validate({**v1, "search": "hybrid", "hybrid": {"alpha": 0.5}})
    stage.ensure_supported()


def test_keyword_properties_are_text_properties_in_the_store() -> None:
    """Every keyword-searchable name is a TEXT property, product_code word-tokenised."""
    by_name = {p.name: p for p in PROPERTIES}
    for name in KEYWORD_PROPERTIES:
        assert by_name[name].dataType.value == "text", name
    assert by_name["product_code"].tokenization.value == "word"
    assert by_name["region"].tokenization.value == by_name["audience"].tokenization.value
    assert by_name["region"].tokenization.value == "field"


def test_v2_differs_from_v1_only_in_search() -> None:
    """v2 isolates hybrid search: every other setting matches v1."""
    v1, v2 = load_stage("v1"), load_stage("v2")
    search_fields = {"name", "description", "search", "hybrid"}
    same = {k: v for k, v in v1.model_dump().items() if k not in search_fields}
    assert same == {k: v for k, v in v2.model_dump().items() if k not in search_fields}
    assert v2.search == "hybrid" and v2.hybrid is not None
    assert (v2.hybrid.alpha_strategy, v2.hybrid.code_alpha, v2.hybrid.default_alpha) == (
        "dynamic",
        0.25,
        1.0,
    )
    assert v2.collection_name == "Chunks_v2"


# ------------------------------------------------------------------- sweep


def _item(item_id: str, question: str, expected: list[str], tag: str = "normal") -> GoldenItem:
    """A minimal golden item."""
    return GoldenItem(
        id=item_id,
        question=question,
        user_id="cust_uk_01",
        failure_tag=tag,
        topic="t",
        expected_doc_ids=expected,
        reference_answer="r",
        expect_handoff=False,
        must_not_contain=[],
    )


ITEMS = [
    _item("g001", "Reset the XR-2200?", ["xr"], "product_code"),
    _item("g002", "Can I return it?", ["ret"]),
    _item("g003", "Student discount?", []),  # no expected docs: left out of every group
]
VECTOR_RUN = {"g001": ["other", "xr", "xr", "xr", "x"], "g002": ["ret", "x", "x", "x", "x"]}
KEYWORD_RUN = {"g001": ["xr", "xr", "xr", "xr", "xr"], "g002": ["x", "x", "x", "x", "x"]}


def test_question_groups() -> None:
    """Groups only hold items with expected docs; code detection splits them."""
    groups = question_groups(ITEMS, DETECTOR)
    assert {g: [i.id for i in items] for g, items in groups.items()} == {
        "all": ["g001", "g002"],
        "product_code": ["g001"],
        "code detected": ["g001"],
        "no code": ["g002"],
    }


def test_score_group_hand_computed() -> None:
    """g1 rank 2 (RR 0.5) with 3 expected chunks in the top 4; g2 rank 1 with 1."""
    scores = score_group(ITEMS[:2], VECTOR_RUN, top_k=4)
    assert scores["recall_at_k"] == {"count": 2, "total": 2}
    assert scores["hit_at_1"] == {"count": 1, "total": 2}
    assert scores["mrr"] == {"value": 0.75, "total": 2}
    assert scores["context"] == {"count": 4, "total": 8}
    assert context_hits(ITEMS[:1], KEYWORD_RUN, top_k=4) == {"count": 4, "total": 4}


def test_dynamic_run_takes_code_questions_from_the_code_run() -> None:
    """Coded questions come from the code_alpha run, the rest from default_alpha."""
    run = dynamic_run(KEYWORD_RUN, VECTOR_RUN, ITEMS[:2], DETECTOR)
    assert run == {"g001": KEYWORD_RUN["g001"], "g002": VECTOR_RUN["g002"]}


def test_sweep_tables_render() -> None:
    """One cell per group (recall@5 · hit@1 · MRR · ctx) and a dynamic matrix."""
    groups = question_groups(ITEMS, DETECTOR)
    table = sweep_table([("vector", VECTOR_RUN)], groups, top_k=4).splitlines()
    assert table[0].startswith("| Setting | all (n=2) | product_code (n=1) |")
    assert table[2] == (
        "| vector | 2/2 · 1/2 · 0.75 · 4/8 | 1/1 · 0/1 · 0.50 · 3/4 | "
        "1/1 · 0/1 · 0.50 · 3/4 | 1/1 · 1/1 · 1.00 · 1/4 |"
    )
    matrix = dynamic_matrix({0.0: KEYWORD_RUN, 1.0: VECTOR_RUN}, groups["all"], DETECTOR)
    rows = matrix.splitlines()
    assert rows[0] == "| code_alpha \\ default_alpha | 0 | 1 |"
    assert rows[2].startswith("| 0 | 1/2 · 1/2 · 0.50 · 4/8 | 2/2 · 2/2 · 1.00 · 5/8 |")

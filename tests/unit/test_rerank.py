"""Tests for cross-encoder reranking, index reuse and the rerank analysis (no network)."""

from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path
from types import ModuleType

import pytest
import yaml
from pydantic import ValidationError

from rag_assistant.config import STAGES_DIR, StageConfig, check_index_owner, load_stage
from rag_assistant.eval.metrics import (
    Judgement,
    ResultRow,
    RetrievedRef,
    check_item,
    latency_summary,
    miss_type,
    summarise_rows,
)
from rag_assistant.eval.report import overall_table
from rag_assistant.eval.rerank_eval import (
    BestScores,
    best_scores,
    latency_stats,
    rank_table,
    suggest_min_score,
    threshold_table,
)
from rag_assistant.eval.sweep import score_group
from rag_assistant.ingestion.pipeline import ingest
from rag_assistant.pipeline import Pipeline
from rag_assistant.retrieval.rerank import Reranker, fuse_chunks, rerank_chunks, sigmoid
from rag_assistant.retrieval.vector import RetrievedChunk
from rag_assistant.schemas import GoldenItem, User

CUSTOMER = User(user_id="c", name="C", role="customer", region="UK")
INGEST_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "ingest.py"


def _chunk(doc_id: str, text: str, index: int = 0) -> RetrievedChunk:
    """A retrieved chunk with a search score that decreases with ``index``."""
    return RetrievedChunk(
        text=text,
        doc_id=doc_id,
        title=doc_id,
        chunk_index=index,
        audience="public",
        region="ALL",
        score=1.0 - index / 100,
    )


def _item(expected: list[str], item_id: str = "g001", tag: str = "normal") -> GoldenItem:
    """A minimal golden item."""
    return GoldenItem(
        id=item_id,
        question="q?",
        user_id="c",
        failure_tag=tag,
        topic="t",
        expected_doc_ids=expected,
        reference_answer="r",
        expect_handoff=False,
        must_not_contain=[],
    )


def keyword_scorer(query: str, documents: list[str]) -> list[float]:
    """Fake cross-encoder: +5 per occurrence of 'warranty', -5 otherwise."""
    del query
    return [5.0 * d.count("warranty") or -5.0 for d in documents]


# ------------------------------------------------------------ normalisation


def test_sigmoid_maps_logits_to_0_1() -> None:
    """0 -> 0.5; large logits saturate without overflow."""
    assert sigmoid(0.0) == 0.5
    assert sigmoid(2.0) == pytest.approx(1 / (1 + math.exp(-2)))
    assert sigmoid(-2.0) == pytest.approx(1 - sigmoid(2.0))
    assert sigmoid(1000.0) == 1.0
    assert sigmoid(-1000.0) == 0.0


# ------------------------------------------------------------------ rerank


def test_rerank_reorders_a_hand_made_candidate_list() -> None:
    """The warranty chunk ranked 3rd by search moves to 1st; scores are attached."""
    candidates = [
        _chunk("manual-vm-55q", "Lines on screen: check the HDMI cable", 0),
        _chunk("faq-007", "My TV shows lines", 1),
        _chunk("policy-warranty", "Panel defects warranty: 24 months warranty", 2),
    ]
    ranked = rerank_chunks("TV lines?", candidates, keyword_scorer)
    assert [c.doc_id for c in ranked] == ["policy-warranty", "manual-vm-55q", "faq-007"]
    assert ranked[0].rerank_logit == 10.0
    assert ranked[0].rerank_score == pytest.approx(sigmoid(10.0))
    assert ranked[0].score == candidates[2].score  # the search score is kept
    assert candidates[2].rerank_score is None  # inputs are not mutated


def test_rerank_ties_keep_search_order() -> None:
    """Equal scores keep their search order (stable sort)."""
    candidates = [_chunk(f"d{n}", "same", n) for n in range(4)]
    assert [c.doc_id for c in rerank_chunks("q", candidates, keyword_scorer)] == [
        "d0",
        "d1",
        "d2",
        "d3",
    ]


def test_rerank_edge_cases() -> None:
    """No candidates gives no output; a scorer returning the wrong count fails loudly."""
    assert rerank_chunks("q", [], keyword_scorer) == []
    with pytest.raises(ValueError, match="2 scores for 1 chunks"):
        rerank_chunks("q", [_chunk("d", "t")], lambda q, d: [1.0, 2.0])


def test_reranker_uses_an_injected_scorer_and_warms_up() -> None:
    """The model is never loaded when a scorer is injected; warm_up scores once."""
    calls: list[list[str]] = []

    def scorer(query: str, documents: list[str]) -> list[float]:
        calls.append(documents)
        return keyword_scorer(query, documents)

    reranker = Reranker("fake-model", scorer=scorer)
    reranker.warm_up()
    ranked = reranker.rerank("q", [_chunk("a", "x"), _chunk("b", "warranty", 1)])
    assert [c.doc_id for c in ranked] == ["b", "a"]
    assert calls[0] == ["warm up"] and len(calls) == 2


# ---------------------------------------------------------------- pipeline


class FakeRetriever:
    """Returns ``k`` numbered chunks; the last one mentions the warranty."""

    def __init__(self) -> None:
        """Remember the requested k values."""
        self.ks: list[int] = []

    def search(self, query: str, k: int) -> list[RetrievedChunk]:
        """``k`` chunks, best search score first."""
        del query
        self.ks.append(k)
        return [_chunk(f"d{n}", "warranty" if n == k - 1 else "other", n) for n in range(k)]


def _rerank_stage(**overrides: object) -> StageConfig:
    """The committed v3 stage, optionally modified."""
    return load_stage("v3").model_copy(update=overrides)


def test_pipeline_fetches_candidates_then_reranks() -> None:
    """A reranked stage searches fetch_k candidates and times both steps."""
    retriever = FakeRetriever()
    reranker = Reranker("fake", scorer=keyword_scorer)
    stage = _rerank_stage(rerank_fusion="replace", rerank_blend_weight=None)
    pipeline = Pipeline(stage, retriever, generator=None, reranker=reranker)  # type: ignore[arg-type]
    retrieval = pipeline.retrieve_ranked("q", k=5)
    assert retriever.ks == [20]
    assert len(retrieval.candidates) == 20 and len(retrieval.ranked) == 5
    assert retrieval.ranked[0].doc_id == "d19"  # the last search result, reranked first
    assert set(retrieval.timings_ms) == {"retrieve", "rerank"}


def test_pipeline_blend_keeps_search_evidence() -> None:
    """v3 blends: the search's 1st result, scored -5 by the reranker, still beats a
    warranty chunk the search ranked 20th, but the raw scores stay on every chunk."""
    pipeline = Pipeline(  # type: ignore[arg-type]
        _rerank_stage(), FakeRetriever(), generator=None, reranker=Reranker("f", keyword_scorer)
    )
    ranked = pipeline.retrieve_ranked("q", k=20).ranked
    assert ranked[0].doc_id == "d0"
    d19 = next(c for c in ranked if c.doc_id == "d19")
    assert (d19.rerank_logit, d19.rerank_score) == (5.0, pytest.approx(sigmoid(5.0)))
    assert all(c.rerank_score is not None for c in ranked)


def test_fuse_chunks_orders_by_fusion_and_keeps_raw_scores() -> None:
    """rrf and blend reorder without discarding the logit, the 0-1 score or the search score."""
    chunks = [
        _chunk("a", "x", 0),  # search rank 1, logit -5
        _chunk("b", "warranty", 1),  # search rank 2, logit 5
        _chunk("c", "warranty warranty", 2),  # search rank 3, logit 10
    ]
    assert [c.doc_id for c in fuse_chunks("q", chunks, keyword_scorer, "replace")] == [
        "c",
        "b",
        "a",
    ]
    # rrf: a = 1/61 + 1/63, b = 1/62 + 1/62, c = 1/63 + 1/61; a and c tie, a wins on search order
    assert [c.doc_id for c in fuse_chunks("q", chunks, keyword_scorer, "rrf")] == ["a", "c", "b"]
    blended = fuse_chunks("q", chunks, keyword_scorer, "blend", weight=0.5)
    # blend: search 1.0/0.99/0.98 min-max to 1/0.5/0, so b = 0.5*sigmoid(5) + 0.25 = 0.747,
    # a = 0.5*sigmoid(-5) + 0.5 = 0.503, c = 0.5*sigmoid(10) + 0 = 0.500
    assert [c.doc_id for c in blended] == ["b", "a", "c"]
    assert {c.doc_id: (c.rerank_logit, c.score) for c in blended} == {
        "a": (-5.0, 1.0),
        "b": (5.0, 0.99),
        "c": (10.0, 0.98),
    }
    assert fuse_chunks("q", [], keyword_scorer, "blend") == []


def test_pipeline_without_rerank_searches_k_only() -> None:
    """A stage without reranking searches exactly k and has no candidates."""
    retriever = FakeRetriever()
    pipeline = Pipeline(load_stage("v2"), retriever, generator=None)  # type: ignore[arg-type]
    retrieval = pipeline.retrieve_ranked("q", k=5)
    assert retriever.ks == [5] and retrieval.candidates == []
    assert set(retrieval.timings_ms) == {"retrieve"}


def test_pipeline_needs_a_reranker_exactly_when_reranking() -> None:
    """Passing a reranker to a non-rerank stage, or none to a rerank stage, fails."""
    with pytest.raises(ValueError, match="reranker"):
        Pipeline(_rerank_stage(), FakeRetriever(), generator=None)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="reranker"):
        Pipeline(load_stage("v2"), FakeRetriever(), None, reranker=Reranker("x"))  # type: ignore[arg-type]


# ------------------------------------------------------------------ config


def _v2_dict() -> dict:
    """The committed v2 stage as a plain dict."""
    return yaml.safe_load((STAGES_DIR / "v2.yaml").read_text(encoding="utf-8"))


RERANK = {
    "rerank": True,
    "rerank_model": "Xenova/ms-marco-MiniLM-L-6-v2",
    "fetch_k": 20,
    "rerank_fusion": "replace",
}


@pytest.mark.parametrize(
    "overrides",
    [
        {"rerank": True},  # no model or fetch_k
        {"rerank": True, "rerank_model": "m"},  # no fetch_k
        {**RERANK, "fetch_k": 3},  # below max(top_k, 5)
        {"rerank_model": "m"},  # model without rerank: true
        {"fetch_k": 20},
        {**RERANK, "min_score": 1.5},
        {**RERANK, "index": "v2"},  # not a collection name
        {**RERANK, "rerank_model": ""},
        {**RERANK, "rerank_fusion": None},  # fusion is required
        {**RERANK, "rerank_fusion": "blend"},  # blend needs a weight
        {**RERANK, "rerank_blend_weight": 0.5},  # a weight needs blend
        {**RERANK, "rerank_fusion": "blend", "rerank_blend_weight": 1.5},
        {**RERANK, "rerank_fusion": "max"},
        {"rerank_fusion": "rrf"},  # rerank: false
    ],
)
def test_invalid_rerank_settings_rejected(overrides: dict) -> None:
    """Rerank settings need rerank: true, a model, fetch_k >= 5 and a 0-1 min_score."""
    with pytest.raises(ValidationError):
        StageConfig.model_validate({**_v2_dict(), **overrides})


def test_v3_differs_from_v2_only_in_rerank_and_index() -> None:
    """v3 isolates reranking: same search, chunking and top_k as v2, and v2's index."""
    v2, v3 = load_stage("v2"), load_stage("v3")
    changed = {"name", "description", "rerank", "rerank_model", "fetch_k", "min_score", "index"}
    changed |= {"rerank_fusion", "rerank_blend_weight"}
    same = {k: v for k, v in v2.model_dump().items() if k not in changed}
    assert same == {k: v for k, v in v3.model_dump().items() if k not in changed}
    assert (v3.rerank, v3.rerank_model, v3.fetch_k, v3.min_score) == (
        True,
        "Xenova/ms-marco-MiniLM-L-6-v2",
        20,
        None,
    )
    assert (v3.rerank_fusion, v3.rerank_blend_weight) == ("blend", 0.5)
    assert (v3.collection_name, v3.index_owner, v3.reuses_index) == ("Chunks_v2", "v2", True)
    assert (v2.collection_name, v2.reuses_index) == ("Chunks_v2", False)


def test_min_score_is_not_implemented_yet() -> None:
    """Setting min_score on a reranked stage names Session 8."""
    stage = StageConfig.model_validate({**_v2_dict(), **RERANK, "name": "x", "min_score": 0.1})
    with pytest.raises(NotImplementedError, match="min_score=0.1.*Session 8"):
        stage.ensure_supported()


def _write(tmp_path: Path, name: str, **overrides: object) -> None:
    """Write a modified v2 stage file as ``name``."""
    data = {**_v2_dict(), "name": name, **overrides}
    (tmp_path / f"{name}.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")


def test_index_reuse_checks_the_owner(tmp_path: Path) -> None:
    """The owner must exist, own its index, and match every ingestion setting."""
    _write(tmp_path, "base")
    _write(tmp_path, "good", index="Chunks_base", **RERANK)
    assert load_stage("good", tmp_path).collection_name == "Chunks_base"

    _write(tmp_path, "orphan", index="Chunks_missing")
    with pytest.raises(ValueError, match="no stage missing.yaml"):
        load_stage("orphan", tmp_path)

    _write(tmp_path, "resized", index="Chunks_base", chunk_size=800)
    with pytest.raises(ValueError, match="chunk_size differ from base"):
        load_stage("resized", tmp_path)

    _write(tmp_path, "chained", index="Chunks_good")
    with pytest.raises(ValueError, match="itself reuses an index"):
        check_index_owner(
            StageConfig.model_validate({**_v2_dict(), "name": "chained", "index": "Chunks_good"}),
            tmp_path,
        )


def test_ingest_refuses_a_reused_index() -> None:
    """ingest() never rebuilds another stage's collection."""
    with pytest.raises(ValueError, match="ingest stage v2 instead"):
        ingest(load_stage("v3"), settings=None)  # type: ignore[arg-type]


def _ingest_script() -> ModuleType:
    """Import scripts/ingest.py as a module (scripts/ is not a package)."""
    spec = importlib.util.spec_from_file_location("ingest_script", INGEST_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ingest_script_skips_v3(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`ingest.py --stage v3` explains the reuse and exits 0 without touching Weaviate."""
    script = _ingest_script()
    monkeypatch.setattr(sys, "argv", ["ingest.py", "--stage", "v3"])
    monkeypatch.setattr(script, "ingest", lambda *a, **k: pytest.fail("must not ingest"))
    monkeypatch.setattr(script, "get_settings", lambda: pytest.fail("must not load settings"))
    assert script.main() == 0
    assert "reuses Chunks_v2 (built by stage v2)" in capsys.readouterr().out


# -------------------------------------------------------------- evaluation


def _row(**overrides: object) -> ResultRow:
    """A result row; override any field."""
    data = {
        "id": "g001",
        "question": "q",
        "user_id": "c",
        "user_role": "customer",
        "user_region": "UK",
        "failure_tag": "normal",
        "topic": "t",
        "history_turns": 0,
        "expected_doc_ids": ["a"],
        "must_not_contain": [],
        "expect_handoff": False,
        "retrieved": [],
        "context_doc_ids": [],
        "context_audiences": [],
        "answer": "x",
        "checks": check_item(_item(["a"]), CUSTOMER, [], [], "x"),
        "correctness": Judgement(verdict="CORRECT", reason="ok"),
        "handoff": None,
        "latency_ms": {"total": 100.0},
    }
    return ResultRow.model_validate({**data, **overrides})


def test_candidate_recall() -> None:
    """Hit if an expected doc is anywhere among the candidates; n/a without reranking."""
    item = _item(["a"])
    candidates = ["x"] * 19 + ["a"]  # 20th candidate
    assert check_item(item, CUSTOMER, ["x"], [], "", candidates).candidate_recall is True
    assert check_item(item, CUSTOMER, ["x"], [], "", ["x"] * 20).candidate_recall is False
    assert check_item(item, CUSTOMER, ["x"], [], "").candidate_recall is None
    assert check_item(_item([]), CUSTOMER, ["x"], [], "", ["a"]).candidate_recall is None


def _ref(doc_id: str) -> RetrievedRef:
    """A stored retrieved chunk."""
    return RetrievedRef(doc_id=doc_id, chunk_index=0, audience="public", region="ALL", score=0.5)


def test_miss_type_classification() -> None:
    """in context / ranking miss / search miss, with and without reranker candidates."""
    assert miss_type(_row(context_doc_ids=["x", "a"])) == "in context"
    assert miss_type(_row(context_doc_ids=["x"], candidate_doc_ids=["x", "a"])) == "ranking miss"
    assert miss_type(_row(context_doc_ids=["x"], candidate_doc_ids=["x", "y"])) == "search miss"
    # without reranking the stored top 5 stand in for the candidates
    assert miss_type(_row(context_doc_ids=["x"], retrieved=[_ref("x"), _ref("a")])) == (
        "ranking miss"
    )
    assert miss_type(_row(context_doc_ids=["x"], retrieved=[_ref("x")])) == "search miss"
    assert miss_type(_row(expected_doc_ids=[])) is None


def test_latency_summary_adds_rerank_stats_when_present() -> None:
    """Rerank mean/p95 appear only for reranked runs."""
    plain = [_row(latency_ms={"total": 100.0})]
    assert "rerank" not in latency_summary(plain)
    reranked = [_row(latency_ms={"total": 1000.0, "rerank": ms}) for ms in (100.0, 200.0, 600.0)]
    summary = latency_summary(reranked)
    assert summary["rerank"] == {"mean": 300.0, "p95": pytest.approx(560.0), "n": 3}
    assert summary["rerank_mean"] == 300.0
    assert summary["rerank_p95"] == pytest.approx(560.0)


def _timed(generate: float, wait: float, requests: float) -> ResultRow:
    """A row with Session 5 step timings."""
    return _row(
        latency_ms={
            "retrieve": 10.0,
            "generate": generate,
            "generate_wait": wait,
            "generate_requests": requests,
            "total": 10.0 + generate + wait,
        }
    )


def test_latency_summary_uses_uncached_generation_without_waits() -> None:
    """Cache hits (no request) are left out of generation; waits are summarised separately."""
    rows = [_timed(800.0, 30_000.0, 1), _timed(1200.0, 0.0, 2), _timed(3.0, 0.0, 0)]
    summary = latency_summary(rows)
    assert summary["generate_excludes_waits"] is True
    assert summary["generate"] == {"mean": 1000.0, "p95": pytest.approx(1180.0), "n": 2}
    assert summary["generate_wait"]["mean"] == 10_000.0
    assert summary["retrieve"]["mean"] == 10.0
    table = overall_table(summarise_rows(rows), k=5)
    assert (
        "| Generation latency mean / p95 (ms), uncached calls, throttle waits excluded (n=2) "
        "| 1000 / 1180 | lower |"
    ) in table


def test_latency_summary_flags_old_runs_that_include_waits() -> None:
    """Rows recorded before Session 5 have no generate_wait: flagged, not guessed."""
    rows = [_row(latency_ms={"retrieve": 10.0, "generate": 900.0, "total": 910.0})]
    summary = latency_summary(rows)
    assert summary["generate_excludes_waits"] is False
    assert summary["generate"]["mean"] == 900.0
    assert "includes throttle waits and cache hits" in overall_table(summarise_rows(rows), k=5)


def test_report_shows_candidate_recall_and_rerank_latency() -> None:
    """The overall table gains both rows for a reranked run."""
    rows = [
        _row(
            checks=check_item(_item(["a"]), CUSTOMER, ["a"], [], "x", ["a"]),
            latency_ms={"total": 900.0, "rerank": 400.0},
            candidate_doc_ids=["a"],
        )
    ]
    table = overall_table(summarise_rows(rows), k=5)
    assert (
        "| Candidate recall (any expected doc among the reranker's fetch_k inputs) | 1/1" in table
    )
    assert "| Rerank latency mean / p95 (ms) | 400 / 400 | lower |" in table


# ----------------------------------------------------------------- analysis


def test_score_group_cuts_long_runs_to_the_top_5() -> None:
    """Recall and MRR always mean top 5, even when a run holds all 20 candidates."""
    item = _item(["a"])
    run = {"g001": ["x"] * 6 + ["a"]}  # expected doc at rank 7
    scores = score_group([item], run)
    assert scores["recall_at_k"] == {"count": 0, "total": 1}
    assert scores["mrr"] == {"value": 0.0, "total": 1}


def test_latency_stats() -> None:
    """Mean and nearest-rank p95."""
    assert latency_stats([100.0] * 19 + [1000.0]) == {"mean": 145.0, "p95": 100.0}
    assert latency_stats([]) == {"mean": 0.0, "p95": 0.0}


def test_rank_table_lists_only_questions_that_differ() -> None:
    """One row per question whose first expected rank differs between runs."""
    items = [_item(["a"], "g001"), _item(["b"], "g002")]
    runs = {"search": {"g001": ["x", "a"], "g002": ["b"]}, "rerank": {"g001": ["a"], "g002": ["b"]}}
    lines = rank_table(items, runs).splitlines()
    assert lines[0] == "| id | tag | question | search | rerank |"
    assert lines[2:] == ["| g001 | normal | q? | 2 | 1 |"]


def test_best_scores() -> None:
    """Best expected-doc chunk vs best other chunk, using the 0-1 score."""
    chunks = rerank_chunks(
        "q",
        [_chunk("a", "warranty"), _chunk("b", "warranty warranty", 1), _chunk("a", "x", 2)],
        keyword_scorer,
    )
    best = best_scores(_item(["a"]), chunks)
    assert best.best_expected == pytest.approx(sigmoid(5.0))
    assert (best.best_other, best.best_other_doc) == (pytest.approx(sigmoid(10.0)), "b")
    assert best_scores(_item([]), chunks).best_expected is None


def _scores(item_id: str, expected: float | None, other: float) -> BestScores:
    """A BestScores record."""
    return BestScores(item_id, "normal", expected, other, "d")


def test_threshold_table_and_suggestion() -> None:
    """Pick the threshold rejecting most unanswerable items while keeping every answer."""
    answerable = [_scores("g001", 0.9, 0.2), _scores("g002", 0.4, 0.3)]
    unanswerable = [_scores("g003", None, 0.05), _scores("g004", None, 0.35)]
    thresholds = (0.1, 0.3, 0.5)
    lines = threshold_table(answerable, unanswerable, thresholds).splitlines()
    assert lines[2:] == ["| 0.1 | 2/2 | 1/2 |", "| 0.3 | 2/2 | 1/2 |", "| 0.5 | 1/2 | 2/2 |"]
    assert suggest_min_score(answerable, unanswerable, thresholds) == 0.1  # tie -> lower

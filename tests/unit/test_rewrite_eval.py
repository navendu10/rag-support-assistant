"""Tests for recording and reporting query rewrites in evaluation runs (no network)."""

from __future__ import annotations

from rag_assistant.eval.compare import stage_sort_key
from rag_assistant.eval.metrics import (
    ItemChecks,
    Judgement,
    ResultRow,
    RewriteRecord,
    latency_summary,
    summarise_rows,
)
from rag_assistant.eval.report import overall_table, rewrite_table
from rag_assistant.eval.rewrites import (
    RewriteChecker,
    rank_changes,
    rewrites_table,
    score_retrieval,
    short_history,
    stored_entries,
)
from rag_assistant.query.rewrite import Catalogue, Rewrite, RewriteResult
from rag_assistant.retrieval.codes import CodeDetector
from rag_assistant.schemas import GoldenItem, Product, Turn

CHECKER = RewriteChecker(
    Catalogue((Product(code="NB-14", name="NoteBook 14 Laptop", category="Laptops"),)),
    CodeDetector.from_codes(["NB-14"]),
)


def _item(
    item_id: str = "g047", question: str = "Can I return a laptop?", **kw: object
) -> GoldenItem:
    """A golden item with defaults."""
    data = {
        "id": item_id,
        "question": question,
        "user_id": "cust_uk_01",
        "failure_tag": "query_region",
        "topic": "returns",
        "expected_doc_ids": ["returns-uk-2026"],
        "reference_answer": "30 days.",
        "expect_handoff": False,
        "must_not_contain": [],
    }
    return GoldenItem.model_validate({**data, **kw})


def _record(**kw: object) -> RewriteRecord:
    """A stored rewrite with defaults."""
    data = {
        "standalone_question": "Can I return a laptop?",
        "search_query": "Laptops return window",
        "product_codes": [],
        "product_category": "Laptops",
        "fallback": False,
        "attempts": 1,
    }
    return RewriteRecord.model_validate({**data, **kw})


def _row(item_id: str = "g047", record: RewriteRecord | None = None, **latency: float) -> ResultRow:
    """A result row carrying ``record`` as its stored rewrite."""
    return ResultRow(
        id=item_id,
        question="Can I return a laptop?",
        user_id="cust_uk_01",
        user_role="customer",
        user_region="UK",
        failure_tag="query_region",
        topic="returns",
        history_turns=0,
        expected_doc_ids=["returns-uk-2026"],
        must_not_contain=[],
        expect_handoff=False,
        retrieved=[],
        context_doc_ids=[],
        context_audiences=[],
        answer="a",
        checks=ItemChecks(
            recall_hit=True,
            retrieval_leak=False,
            answer_leak=None,
            injection_fail=None,
            forbidden_hits=[],
        ),
        correctness=Judgement(verdict="CORRECT", reason="ok"),
        handoff=None,
        latency_ms={"total": 100.0, **latency},
        rewrite=record,
    )


def test_stages_sort_with_lettered_steps_first() -> None:
    """v4a (rewriting only) is listed before v4 (rewriting + filter), and v10 after v9."""
    names = ["v4", "v10", "v3", "v4a", "v9", "custom"]
    assert sorted(names, key=stage_sort_key) == ["v3", "v4a", "v4", "v9", "v10", "custom"]


def test_checker_records_and_flags_a_rewrite() -> None:
    """The stored record keeps every field and flags the invented NoteBook 14."""
    result = RewriteResult(
        Rewrite(
            standalone_question="Can I return the NoteBook 14?",
            search_query="Laptops return window",
            product_codes=["NB-14"],
            product_category="Laptops",
        ),
        fallback=False,
        attempts=2,
    )
    record = CHECKER.record(result, _item())
    assert (record.product_codes, record.attempts) == (["NB-14"], 2)
    assert record.flags == ["invented product NB-14"]
    history = [Turn(role="user", content="I bought the NoteBook 14.")]
    assert CHECKER.reflag(record, _item(history=history)).flags == []


def test_summary_counts_fallbacks_and_flags_and_times_rewrites() -> None:
    """Fallbacks and flagged rewrites are rates; rewrite latency counts uncached calls only."""
    rows = [
        _row("g001", _record(), rewrite=700.0, rewrite_wait=0.0, rewrite_requests=1.0),
        _row("g002", _record(fallback=True), rewrite=5.0, rewrite_wait=0.0, rewrite_requests=0.0),
        _row("g003", _record(flags=["dropped country UK"])),
        _row("g004", None),
    ]
    summary = summarise_rows(rows)
    assert summary["rewrite_fallbacks"] == {"count": 1, "total": 3}
    assert summary["rewrite_flagged"] == {"count": 1, "total": 3}
    assert latency_summary(rows)["rewrite"] == {"mean": 700.0, "p95": 700.0, "n": 1}
    table = overall_table(summary, 5)
    assert "Rewrite fallbacks" in table and "1/3 (33%)" in table
    assert "Rewrite latency mean / p95 (ms)" in table and "(n=1)" in table


def test_stages_without_rewriting_show_no_rewrite_rows() -> None:
    """Earlier stages' reports are unchanged: no rewrite rows."""
    table = overall_table(summarise_rows([_row("g001", None)]), 5)
    assert "Rewrite" not in table


def test_report_lists_only_flagged_or_fallen_back_rewrites() -> None:
    """report.md's rewrite section skips clean rewrites."""
    rows = [_row("g001", _record()), _row("g002", _record(flags=["invented product NB-14"]))]
    table = rewrite_table(rows)
    assert "g002" in table and "g001" not in table
    assert rewrite_table([_row("g001", _record())]) == "No rewrite was flagged or fell back."


def test_show_rewrites_table_has_every_rewrite_with_short_history() -> None:
    """One row per rewritten question; long histories are cut; stored rows pair with items."""
    long = "I bought the NoteBook 14 last week and I have a question about it, please help"
    item = _item(history=[Turn(role="user", content=long)])
    assert short_history(item, 30).endswith("…") and len(short_history(item, 30)) <= 30
    assert short_history(_item()) == "-"
    record = _record(fallback=True)
    table = rewrites_table(stored_entries([_row("g047", record), _row("g001", None)], [item]))
    assert "| g047 |" in table and "FALLBACK" in table and "Laptops return window" in table
    assert "g001" not in table


def test_retrieval_scores_and_rank_changes() -> None:
    """Recall@5, hit@1 and MRR over items with expected docs, and which ranks moved."""
    expected = {"g001": ["a"], "g002": ["b"], "g003": []}
    before = {"g001": ["x", "a"], "g002": ["b"], "g003": ["z"]}
    after = {"g001": ["a"], "g002": ["x", "y"], "g003": ["z"]}
    score = score_retrieval(expected, before)
    assert (score.recall, score.hit1, score.mrr, score.total) == (2, 1, 0.75, 2)
    assert score.cell() == "2/2 · 1 · 0.75"
    assert rank_changes(expected, before, after) == [("g001", 2, 1), ("g002", 1, None)]

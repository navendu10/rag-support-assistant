"""Unit tests for the Session 8 metrics (false handoffs, invented content, citation
validity, corrective-flow retries) and the offline context rebuild. No network."""

from __future__ import annotations

from pathlib import Path

import pytest

from rag_assistant.config import load_stage
from rag_assistant.eval.contexts import ContextMismatchError, chunk_lookup, stored_context
from rag_assistant.eval.metrics import (
    GroundedRecord,
    ItemChecks,
    Judgement,
    PathRecord,
    ResultRow,
    RetrievedRef,
    false_handoff_rows,
    invented_input,
    summarise_rows,
)
from rag_assistant.eval.report import render_report
from rag_assistant.eval.runner import FIXED_MESSAGE_VERDICT, judge_invented, load_rows, rescore_row
from rag_assistant.schemas import GoldenItem, User

PROJECT_ROOT = Path(__file__).resolve().parents[2]
HANDOFF = Judgement(verdict="HANDOFF", reason="offers an agent")
ANSWERED = Judgement(verdict="ANSWERED", reason="answers")


def _row(**overrides: object) -> ResultRow:
    """A result row with defaults (an answerable customer question)."""
    data = {
        "id": "g030",
        "question": "q",
        "user_id": "cust_uk_01",
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
        "answer": "answer",
        "checks": ItemChecks(
            recall_hit=True,
            retrieval_leak=False,
            answer_leak=None,
            injection_fail=None,
            forbidden_hits=[],
        ),
        "correctness": Judgement(verdict="CORRECT", reason="ok"),
        "handoff": ANSWERED,
        "latency_ms": {"total": 1.0},
    }
    return ResultRow.model_validate({**data, **overrides})


def _path(handoff: str | None = None, retry: bool = False) -> PathRecord:
    """A corrective-flow route."""
    return PathRecord(
        grade="low" if retry else "pass",
        best_score=0.5,
        retry_used=retry,
        handoff_reason=handoff,
        ticket_created=handoff is not None,
    )


def test_false_handoffs_use_the_flow_route_when_there_is_one() -> None:
    """v6: the flow's route decides; older stages: the handoff judge; handoff items: n/a."""
    rows = [
        _row(id="g030", path=_path("not_found"), handoff=ANSWERED),  # route wins over judge
        _row(id="g031", path=_path(None), handoff=HANDOFF),
        _row(id="g032", handoff=HANDOFF),  # no flow: the judge
        _row(id="g033", handoff=ANSWERED),
        _row(id="g034", handoff=None),  # not judged yet: n/a
        _row(id="g022", expect_handoff=True, handoff=HANDOFF),  # expected: never "false"
    ]
    assert summarise_rows(rows)["false_handoffs"] == {"count": 2, "total": 4}
    assert [r.id for r in false_handoff_rows(rows)] == ["g030", "g032"]
    report = render_report(
        {
            "stage": "x",
            "date": "d",
            "recall_k": 5,
            "top_k": 4,
            "generation_model": "m",
            "judge_model": "j",
            "reasoning_effort": "low",
        },
        summarise_rows(rows),
        {},
        rows,
    )
    assert "## False handoffs" in report and "flow: not_found (grade pass)" in report
    assert "judge: offers an agent" in report


def test_invented_content_counts_unparseable_as_invented() -> None:
    """INVENTED and an unparseable verdict count; SUPPORTED doesn't; unjudged is n/a."""
    rows = [
        _row(invented=Judgement(verdict="INVENTED", reason="adds a contact")),
        _row(invented=Judgement(verdict=None, reason="unparseable")),
        _row(invented=Judgement(verdict="SUPPORTED", reason="ok")),
        _row(),
    ]
    summary = summarise_rows(rows)
    assert summary["invented_content"] == {"count": 2, "total": 3}
    assert summary["judge_errors"] == 1


def test_invented_judge_reads_the_model_text_and_skips_a_fixed_message() -> None:
    """The judge reads what the model wrote: the shown not-found text, not our message."""
    shown = _row(
        answer="Not found [1].\n\nI've passed your question...", model_text="Not found [1]."
    )
    assert invented_input(shown) == "Not found [1]."
    assert invented_input(_row(answer="plain")) == "plain"
    fixed = _row(answer="I've passed your question...", fixed_message=True)
    assert invented_input(fixed) is None

    class NoCalls:
        def invented(self, *args: object) -> Judgement:
            raise AssertionError("a fixed message needs no LLM call")

    assert judge_invented(NoCalls(), fixed, "sources") == FIXED_MESSAGE_VERDICT  # type: ignore[arg-type]


def test_citation_validity_and_flow_retry_rates() -> None:
    """Citations valid over grounded rows; retries over flow rows, answerable ones apart."""
    grounded_ok = GroundedRecord(found_in_sources=True, citations=[1], valid=True, attempts=1)
    grounded_retried = GroundedRecord(found_in_sources=True, citations=[2], valid=True, attempts=2)
    grounded_bad = GroundedRecord(found_in_sources=False, citations=[], valid=False, attempts=2)
    rows = [
        _row(grounded=grounded_ok, path=_path(retry=True)),
        _row(grounded=grounded_retried, path=_path()),
        _row(grounded=grounded_bad, path=_path("invalid_output"), expect_handoff=True),
        _row(),  # a naive stage's row: n/a everywhere
    ]
    s = summarise_rows(rows)
    assert s["citations_valid"] == {"count": 2, "total": 3}
    assert s["grounded_reattempts"] == {"count": 2, "total": 3}
    assert s["flow_retries"] == {"count": 1, "total": 3}
    assert s["flow_retries_answerable"] == {"count": 1, "total": 2}
    assert s["flow_handoffs"] == {"count": 1, "total": 3}


def test_old_rows_still_load() -> None:
    """Rows written before Session 8 have none of the new fields."""
    rows = load_rows(PROJECT_ROOT / "results" / "v0")
    assert rows and all(r.grounded is None and r.invented is None for r in rows)


# ------------------------------------------------------------ context rebuild


def test_stored_v5_contexts_rebuild_offline(generated_dir: Path) -> None:
    """Every committed v5 row's context chunks are rebuilt from the generated data, with
    matching sections, so back-filled judges read exactly what the model read."""
    lookup = chunk_lookup(load_stage("v5"), generated_dir)
    rows = load_rows(PROJECT_ROOT / "results" / "v5")
    for row in rows:
        chunks = stored_context(row, lookup)
        assert [c.doc_id for c in chunks] == row.context_doc_ids
    g018 = next(r for r in rows if r.id == "g018")
    assert stored_context(g018, lookup)[0].doc_id == "policy-shipping"


def test_a_mismatched_section_fails_loudly(generated_dir: Path) -> None:
    """A stored chunk whose section differs from the rebuilt one is an error, not a guess."""
    lookup = chunk_lookup(load_stage("v5"), generated_dir)
    row = next(r for r in load_rows(PROJECT_ROOT / "results" / "v5") if r.id == "g018")
    bad = row.retrieved[0].model_copy(update={"section": "Somewhere else"})
    tampered = row.model_copy(update={"retrieved": [bad, *row.retrieved[1:]]})
    with pytest.raises(ContextMismatchError, match="section"):
        stored_context(tampered, lookup)


def test_retry_trigger_counts_and_rescued_retries() -> None:
    """Retries are counted per trigger; a not-found retry is 'rescued' only if it ended in
    an answer AND the judge says CORRECT."""
    wrong = Judgement(verdict="INCORRECT", reason="no")

    def flow(trigger: str, changed: bool | None, handoff: str | None = None) -> PathRecord:
        return _path(handoff, retry=trigger != "none").model_copy(
            update={"retry_trigger": trigger, "retry_changed_outcome": changed}
        )

    rows = [
        _row(path=flow("none", None)),
        _row(path=flow("low_score", None, "not_found"), expect_handoff=True),
        _row(path=flow("not_found", True)),  # rescued: answered and CORRECT
        _row(path=flow("not_found", True), correctness=wrong),  # answered, but wrong
        _row(path=flow("not_found", False, "not_found")),  # still handed off
        _row(),  # no flow: n/a
    ]
    s = summarise_rows(rows)
    assert s["retries_low_score"] == {"count": 1, "total": 5}
    assert s["retries_not_found"] == {"count": 3, "total": 5}
    assert s["retries_rescued"] == {"count": 1, "total": 3}


def test_rescore_reads_only_the_first_recall_k_chunks() -> None:
    """A wider retry stores its whole 8-chunk context; ranking metrics still use the top 5."""

    refs = [
        RetrievedRef(doc_id=d, chunk_index=0, audience="public", region="UK", score=0.5)
        for d in ["x1", "x2", "x3", "x4", "x5", "policy-warranty", "x7", "x8"]
    ]
    row = _row(retrieved=refs, expected_doc_ids=["policy-warranty"])
    item = GoldenItem(
        id="g030",
        question="q",
        user_id="cust_uk_01",
        failure_tag="normal",
        topic="t",
        expected_doc_ids=["policy-warranty"],
        reference_answer="r",
        expect_handoff=False,
        must_not_contain=[],
    )
    user = User(user_id="cust_uk_01", name="O", role="customer", region="UK")
    assert rescore_row(row, item, user, recall_k=5).checks.recall_hit is False
    assert rescore_row(row, item, user, recall_k=8).checks.recall_hit is True

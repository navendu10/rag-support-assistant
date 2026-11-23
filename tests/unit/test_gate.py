"""The CI retrieval gate's decision (Session 13). No network: the checks on its results."""

from __future__ import annotations

import json
from pathlib import Path

from rag_assistant.eval.gate import BASELINE_FILE, GateResult, leaks_for, problems
from rag_assistant.retrieval.vector import RetrievedChunk

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BASELINE = {"recall_hits": 43, "questions": 45}


def _chunk(doc_id: str, audience: str) -> RetrievedChunk:
    return RetrievedChunk("t", doc_id, doc_id, 0, audience, "ALL", 1.0)


def test_the_gate_allows_two_questions_below_the_baseline_and_no_more() -> None:
    """43 -> 41 passes; 40 fails."""
    assert problems(GateResult(41, 45), BASELINE) == []
    assert "below the baseline" in problems(GateResult(40, 45), BASELINE)[0]


def test_any_leak_broken_index_or_llm_request_fails_the_gate() -> None:
    """Each is a failure on its own, whatever recall is."""
    assert problems(GateResult(45, 45, leaks=["g019: pb-fraud-checks#0"]), BASELINE)
    assert problems(GateResult(45, 45, index_misses=["Chunks_v7_public: 3/156"]), BASELINE)
    assert problems(GateResult(45, 45, llm_requests=1), BASELINE)


def test_only_a_customer_seeing_an_internal_chunk_is_a_leak() -> None:
    """Agents may retrieve playbooks; customers must never."""
    chunks = [_chunk("faq-001", "public"), _chunk("pb-fraud-checks", "internal")]
    assert leaks_for("g019", "cust_us_01", "customer", chunks) == [
        "g019: pb-fraud-checks#0 for cust_us_01"
    ]
    assert leaks_for("g048", "agent_01", "agent", chunks) == []


def test_the_committed_baseline_is_readable_and_has_no_leaks() -> None:
    """The file CI compares against."""
    baseline = json.loads((PROJECT_ROOT / BASELINE_FILE).read_text(encoding="utf-8"))
    assert baseline["questions"] == 45 and 0 < baseline["recall_hits"] <= 45
    assert baseline["leaks"] == 0

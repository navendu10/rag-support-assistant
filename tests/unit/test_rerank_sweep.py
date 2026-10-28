"""Tests for reranker input variants, rank fusion and the rerank-sweep helpers (no network)."""

from __future__ import annotations

import pytest

from rag_assistant.eval.rerank_eval import (
    SweepResult,
    contaminated,
    pick_setting,
    regressions,
)
from rag_assistant.retrieval.rerank import fused_order, fusion_scores, sigmoid
from rag_assistant.retrieval.rerank_input import (
    linearise_tables,
    parse_table,
    reranker_inputs,
    row_groups,
    row_sentence,
    split_header,
)
from rag_assistant.schemas import GoldenItem

HEADER = (
    "[VoltCart Returns Policy (UK) > VoltCart Returns Policy - United Kingdom > Return windows]"
)
TABLE_CHUNK = "\n".join(
    [
        HEADER,
        "## Return windows",
        "The table below lists the return window for each product category.",
        "| Product category | Condition | Return window |",
        "|---|---|---|",
        *[f"| Cat{n} | Opened | {n} days |" for n in range(1, 10)],
        "Refunds follow within 5 working days.",
    ]
)


# ------------------------------------------------------------------ inputs


def test_split_header() -> None:
    """Only a bracketed first line counts as a header."""
    assert split_header("[A > B]\nbody") == ("[A > B]", "body")
    assert split_header("no header\nbody") == ("", "no header\nbody")


def test_parse_table_drops_the_separator() -> None:
    """Column names and data rows; the ``|---|`` row is skipped."""
    columns, rows = parse_table("| A | B |\n|---|:-:|\n| 1 | 2 |\n| 3 | |")
    assert columns == ["A", "B"]
    assert rows == [["1", "2"], ["3", ""]]


def test_row_sentence_uses_columns_and_context() -> None:
    """Empty cells are left out."""
    assert row_sentence(
        "UK Returns > Return windows", ["Cat", "Window", "Fee"], ["Audio", "15 days", ""]
    ) == ("UK Returns > Return windows: Cat Audio; Window 15 days.")


def test_linearise_tables_rewrites_rows_and_keeps_prose() -> None:
    """Each table row becomes a sentence with the header's title and last section."""
    text = linearise_tables(TABLE_CHUNK)
    assert "|" not in text.replace(HEADER, "")
    assert (
        "VoltCart Returns Policy (UK) > Return windows: Product category Cat3; "
        "Condition Opened; Return window 3 days."
    ) in text
    assert text.startswith(HEADER)
    assert text.endswith("Refunds follow within 5 working days.")


def test_row_groups_repeat_the_header_and_cover_every_row() -> None:
    """9 rows in groups of 4 -> 3 groups, each with the header, prose and column names."""
    groups = row_groups(TABLE_CHUNK, rows_per_group=4)
    assert len(groups) == 3
    for group in groups:
        assert group.startswith(HEADER)
        assert "| Product category | Condition | Return window |" in group
        assert "Refunds follow within 5 working days." in group
    rows = [line for g in groups for line in g.split("\n") if line.startswith("| Cat")]
    assert rows == [f"| Cat{n} | Opened | {n} days |" for n in range(1, 10)]


def test_short_or_tableless_chunks_are_one_group() -> None:
    """Nothing to split: the chunk is returned unchanged."""
    assert row_groups("[T]\nJust prose.") == ["[T]\nJust prose."]
    short = "[T]\n| A |\n|---|\n| 1 |\n| 2 |"
    assert row_groups(short, rows_per_group=4) == [short]


def test_reranker_inputs_variants() -> None:
    """Every variant returns texts to score; groups variants return several."""
    assert reranker_inputs(TABLE_CHUNK, "chunk") == [TABLE_CHUNK]
    assert reranker_inputs(TABLE_CHUNK, "body")[0].startswith("## Return windows")
    assert len(reranker_inputs(TABLE_CHUNK, "linear")) == 1
    assert len(reranker_inputs(TABLE_CHUNK, "groups")) == 3
    linear_groups = reranker_inputs(TABLE_CHUNK, "linear_groups")
    assert len(linear_groups) == 3 and all("|" not in g.replace(HEADER, "") for g in linear_groups)


# ------------------------------------------------------------------ fusion


SEARCH = [1.0, 0.9, 0.8, 0.7]  # candidates in search order
LOGITS = [-5.0, 3.0, 1.0, 9.0]


def test_replace_orders_by_logit() -> None:
    """Rerank only: the search order is ignored except for ties."""
    assert fused_order(SEARCH, LOGITS, "replace") == [3, 1, 2, 0]
    assert fused_order(SEARCH, [1.0, 1.0, 1.0, 1.0], "replace") == [0, 1, 2, 3]


def test_rrf_hand_computed() -> None:
    """Search ranks 1-4, rerank ranks 4, 2, 3, 1 with k=60."""
    scores = fusion_scores(SEARCH, LOGITS, "rrf")
    assert scores == pytest.approx(
        [1 / 61 + 1 / 64, 1 / 62 + 1 / 62, 1 / 63 + 1 / 63, 1 / 64 + 1 / 61]
    )
    assert fused_order(SEARCH, LOGITS, "rrf") == [1, 0, 3, 2]  # 0 and 3 tie: search order


def test_blend_hand_computed() -> None:
    """0.5 * sigmoid(logit) + 0.5 * min-max(search score)."""
    scores = fusion_scores(SEARCH, LOGITS, "blend")
    expected = [
        0.5 * sigmoid(-5) + 0.5 * 1.0,
        0.5 * sigmoid(3) + 0.5 * (2 / 3),
        0.5 * sigmoid(1) + 0.5 * (1 / 3),
        0.5 * sigmoid(9) + 0.5 * 0.0,
    ]
    assert scores == pytest.approx(expected)
    assert fused_order(SEARCH, LOGITS, "blend") == [1, 2, 0, 3]


def test_fusion_edge_cases() -> None:
    """Empty input, equal search scores, and mismatched lengths."""
    assert fusion_scores([], [], "rrf") == []
    assert fusion_scores([0.5, 0.5], [0.0, 0.0], "blend") == pytest.approx([0.75, 0.75])
    with pytest.raises(ValueError, match="same length"):
        fusion_scores([1.0], [1.0, 2.0], "rrf")


# ------------------------------------------------------------------- sweep


def _item(item_id: str, expected: list[str]) -> GoldenItem:
    """A minimal golden item."""
    return GoldenItem(
        id=item_id,
        question="q?",
        user_id="cust_uk_01",
        failure_tag="normal",
        topic="t",
        expected_doc_ids=expected,
        reference_answer="r",
        expect_handoff=False,
        must_not_contain=[],
    )


def test_regressions_against_the_baseline() -> None:
    """Lost and gained recall@5 and hit@1, per question; n/a items are skipped."""
    items = [_item("g001", ["a"]), _item("g002", ["b"]), _item("g003", [])]
    baseline = {"g001": ["a", "x"], "g002": ["x"] * 6 + ["b"], "g003": ["x"]}
    run = {"g001": ["x"] * 5 + ["a"], "g002": ["b"], "g003": ["y"]}
    reg = regressions(items, baseline, run)
    assert reg.lost_recall == ("g001",) and reg.gained_recall == ("g002",)
    assert reg.lost_hit1 == ("g001",) and reg.gained_hit1 == ("g002",)


def test_contaminated() -> None:
    """Only a coded question with another product's chunk in its context counts."""
    codes = {"manual-xr-2200": "XR-2200", "manual-xr-2000": "XR-2000"}
    assert contaminated(["XR-2200"], ["manual-xr-2200", "manual-xr-2000"], codes)
    assert not contaminated(["XR-2200"], ["manual-xr-2200", "faq-005"], codes)
    assert not contaminated([], ["manual-xr-2000"], codes)
    # an error code names no product, so its own manual is not contamination
    assert not contaminated(["E-74"], ["manual-xr-2200"], codes)
    assert contaminated(["XR-2200", "E-74"], ["manual-xr-2000"], codes)


def test_pick_setting() -> None:
    """Recall floor first, then hit@1, MRR, regressions, contamination, latency."""
    results = [
        SweepResult("high hit1, low recall", 30, 30, 0.9, 0, 0, 0, 10),
        SweepResult("count ok, but loses a question", 36, 28, 0.8, 1, 1, 0, 10),
        SweepResult("ok", 34, 25, 0.75, 0, 3, 1, 1500),
        SweepResult("ok but slower", 34, 25, 0.75, 0, 3, 1, 6000),
        SweepResult("ok, fewer regressions", 35, 25, 0.75, 0, 1, 1, 1500),
    ]
    assert pick_setting(results, min_recall=34).label == "ok, fewer regressions"
    assert pick_setting(results, min_recall=36) is None

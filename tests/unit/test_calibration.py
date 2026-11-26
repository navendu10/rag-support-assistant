"""Judge calibration maths (Session 13 Part B): agreement, kappa, variance. No model."""

from __future__ import annotations

import pytest

from rag_assistant.eval.calibration import (
    CalibrationItem,
    changed_verdicts,
    cohen_kappa,
    majority,
    summarise,
    verdict_label,
)


def test_cohen_kappa_matches_a_worked_example() -> None:
    """20 items: 7+8 agree, 3 and 2 disagree -> observed 0.75, chance 0.5 -> kappa 0.5."""
    truth = ["pass"] * 10 + ["fail"] * 10
    judge = ["pass"] * 7 + ["fail"] * 3 + ["pass"] * 2 + ["fail"] * 8
    assert cohen_kappa(truth, judge) == pytest.approx(0.5, abs=1e-3)


def test_kappa_is_zero_or_undefined_when_one_label_dominates() -> None:
    """The reason for extra fail labels: "always pass" agrees 39/40 yet carries no signal."""
    truth = ["pass"] * 39 + ["fail"]
    assert cohen_kappa(truth, ["pass"] * 40) == 0.0
    assert cohen_kappa(["pass"] * 3, ["pass"] * 3) is None
    with pytest.raises(ValueError):
        cohen_kappa(["pass"], [])


def test_majority_verdict_and_instability() -> None:
    """2 of 3 decides; a tie counts as fail; mixed runs are unstable."""
    assert majority(["pass", "fail", "pass"]) == "pass"
    assert majority(["pass", "fail"]) == "fail"
    assert verdict_label("CORRECT") == "pass" and verdict_label(None) == "fail"
    item = CalibrationItem("v9", "g014", "pass", ("fail", "pass", "pass"), ("a", "b", "c"))
    assert item.verdict == "pass" and item.unstable


def test_summary_and_changed_verdicts() -> None:
    """The confusion table, the disagreements, and what a new prompt changed."""
    before = [
        CalibrationItem("v9", "g014", "pass", ("fail",) * 3, ("omits a detail",) * 3),
        CalibrationItem("v5", "g027", "fail", ("fail",) * 3, ("wrong",) * 3),
        CalibrationItem("v7", "g001", "pass", ("pass",) * 3, ("ok",) * 3),
    ]
    s = summarise(before)
    assert s.confusion == {"pass->pass": 1, "pass->fail": 1, "fail->pass": 0, "fail->fail": 1}
    assert s.agreement == pytest.approx(0.667, abs=1e-3)
    assert [i.item_id for i in s.disagreements] == ["g014"]
    after = [before[1], before[2],
             CalibrationItem("v9", "g014", "pass", ("pass",) * 3, ("ok",) * 3)]  # fmt: skip
    assert changed_verdicts(before, after) == [("v9", "g014", "fail", "pass")]

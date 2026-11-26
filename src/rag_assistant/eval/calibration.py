"""Calibrating the correctness judge against hand labels (Session 13 Part B).

``scripts/calibrate_judge.py`` runs the judge several times on every hand-labelled answer
(``data/golden/judge_labels.jsonl``), with the LLM cache bypassed so the runs can differ.
These pure functions turn the verdicts into:

- **agreement**: the share of items where the majority verdict matches the label;
- **Cohen's kappa**: agreement corrected for chance (1 perfect, 0 chance, < 0 worse);
- **the confusion table** (label x majority verdict);
- **variance**: items whose runs disagreed with each other;
- **disagreements**: each item where the majority verdict differs from the label.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

PASS, FAIL = "pass", "fail"


def verdict_label(verdict: str | None) -> str:
    """CORRECT -> pass; INCORRECT or an unparseable reply -> fail."""
    return PASS if verdict == "CORRECT" else FAIL


def majority(labels: Sequence[str]) -> str:
    """The most common label (a tie counts as fail: the judge isn't sure it's correct)."""
    counts = Counter(labels)
    return PASS if counts[PASS] > counts[FAIL] else FAIL


def cohen_kappa(truth: Sequence[str], predicted: Sequence[str]) -> float | None:
    """Cohen's kappa for two raters over the same items (None if undefined)."""
    if len(truth) != len(predicted) or not truth:
        raise ValueError("kappa needs two equally long, non-empty label lists")
    n = len(truth)
    observed = sum(a == b for a, b in zip(truth, predicted, strict=True)) / n
    expected = sum(
        (truth.count(c) / n) * (predicted.count(c) / n) for c in set(truth) | set(predicted)
    )
    if expected == 1.0:
        return None  # both raters gave one label only: kappa is undefined
    return round((observed - expected) / (1.0 - expected), 3)


@dataclass(frozen=True)
class CalibrationItem:
    """One labelled answer and the judge's runs on it."""

    stage: str
    item_id: str
    label: str
    runs: tuple[str, ...]  # pass/fail per run
    reasons: tuple[str, ...]

    @property
    def verdict(self) -> str:
        """The majority of the runs."""
        return majority(self.runs)

    @property
    def unstable(self) -> bool:
        """The runs disagreed with each other."""
        return len(set(self.runs)) > 1


@dataclass(frozen=True)
class CalibrationSummary:
    """The headline numbers for one prompt."""

    items: int
    agreement: float
    kappa: float | None
    confusion: dict[str, int]  # "pass->fail" etc. (label -> verdict)
    unstable: int
    disagreements: list[CalibrationItem]


def summarise(items: Sequence[CalibrationItem]) -> CalibrationSummary:
    """Agreement, kappa, confusion, variance and the disagreements."""
    truth = [i.label for i in items]
    verdicts = [i.verdict for i in items]
    confusion = Counter(f"{t}->{v}" for t, v in zip(truth, verdicts, strict=True))
    return CalibrationSummary(
        items=len(items),
        agreement=round(sum(t == v for t, v in zip(truth, verdicts, strict=True)) / len(items), 3),
        kappa=cohen_kappa(truth, verdicts),
        confusion={
            k: confusion.get(k, 0) for k in ("pass->pass", "pass->fail", "fail->pass", "fail->fail")
        },
        unstable=sum(i.unstable for i in items),
        disagreements=[i for i in items if i.verdict != i.label],
    )


def changed_verdicts(
    before: Sequence[CalibrationItem], after: Sequence[CalibrationItem]
) -> list[tuple[str, str, str, str]]:
    """(stage, item, old verdict, new verdict) for items whose majority verdict changed."""
    old = {(i.stage, i.item_id): i.verdict for i in before}
    return [
        (i.stage, i.item_id, old[(i.stage, i.item_id)], i.verdict)
        for i in after
        if (i.stage, i.item_id) in old and old[(i.stage, i.item_id)] != i.verdict
    ]

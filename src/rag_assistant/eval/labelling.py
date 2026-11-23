"""Hand labels for judge calibration (Session 13): what scripts/label_answers.py shows.

``select_items`` picks 40 stored answers: every item on the judge calibration list in
``docs/evaluation.md`` (golden rows and agent scenarios, with their stage), then a fixed
random sample (seed 13) of golden rows from v4-v10. ``load_item`` turns one into what a
person needs to judge it: question, reference, answer and sources. Labels are appended to
``data/golden/judge_labels.jsonl`` and are never written by a model.
"""

from __future__ import annotations

import json
import random
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

LABELS_FILE = Path("data/golden/judge_labels.jsonl")
CALIBRATION_HEADING = "## Judge calibration list"
SAMPLE_STAGES = ("v4", "v5", "v6", "v6a", "v7", "v8", "v9", "v10")
TOTAL = 40
SEED = 13
_ID = re.compile(r"\b([gs]\d{2,3})\b")


@dataclass(frozen=True, order=True)
class ItemRef:
    """One stored answer: a stage and a golden id (g...) or scenario id (s...)."""

    stage: str
    item_id: str

    @property
    def kind(self) -> Literal["golden", "scenario"]:
        """Golden row or agent scenario."""
        return "scenario" if self.item_id.startswith("s") else "golden"


class Label(BaseModel):
    """One hand label."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    stage: str
    item_id: str
    kind: Literal["golden", "scenario"]
    label: Literal["pass", "fail"]
    note: str = ""
    labelled: str = Field(default_factory=lambda: date.today().isoformat())


def calibration_refs(evaluation_md: str) -> list[ItemRef]:
    """Every (stage, item) on the calibration list table, in table order, once each."""
    section = evaluation_md.split(CALIBRATION_HEADING, 1)[1].split("\n## ", 1)[0]
    refs: list[ItemRef] = []
    for line in section.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2 or not cells[0].startswith("v"):
            continue
        for stage in (s.strip() for s in cells[0].split(",")):
            for item in _ID.findall(cells[1]):
                ref = ItemRef(stage, item)
                if ref not in refs:
                    refs.append(ref)
    return refs


def select_items(
    calibration: Sequence[ItemRef],
    available: Sequence[ItemRef],
    total: int = TOTAL,
    seed: int = SEED,
    sample_stages: Sequence[str] = SAMPLE_STAGES,
) -> list[ItemRef]:
    """The calibration items (those with a stored answer), then a seeded sample of the
    other stored golden rows of ``sample_stages``, up to ``total``."""
    have = set(available)
    chosen = [r for r in calibration if r in have]
    rest = sorted(
        r
        for r in available
        if r not in set(chosen) and r.kind == "golden" and r.stage in sample_stages
    )
    chosen += random.Random(seed).sample(rest, max(0, min(total - len(chosen), len(rest))))
    return chosen[:total]


def stored_refs(results_dir: Path) -> list[ItemRef]:
    """Every stored golden row and agent scenario under ``results_dir`` (from each stage's
    results.jsonl and agent_results.jsonl)."""
    refs = []
    for stage_dir in sorted(p for p in results_dir.iterdir() if p.is_dir()):
        for name in ("results.jsonl", "agent_results.jsonl"):
            path = stage_dir / name
            if path.is_file():
                for line in path.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        refs.append(ItemRef(stage_dir.name, json.loads(line)["id"]))
    return refs


def load_labels(path: Path) -> list[Label]:
    """Labels recorded so far (none if the file doesn't exist)."""
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    return [Label.model_validate_json(x) for x in lines if x.strip()]


def append_label(path: Path, label: Label) -> None:
    """Add one label (the file is append-only)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(label.model_dump_json() + "\n")


def unlabelled(items: Sequence[ItemRef], labels: Sequence[Label]) -> list[ItemRef]:
    """The items without a label yet (so a session can be resumed)."""
    done = {(x.stage, x.item_id) for x in labels}
    return [r for r in items if (r.stage, r.item_id) not in done]

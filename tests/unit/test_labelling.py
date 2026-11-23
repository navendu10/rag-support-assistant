"""The labelling tool's selection and label file (Session 13). No model is called."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from rag_assistant.eval.labelling import (
    ItemRef,
    Label,
    append_label,
    calibration_refs,
    load_labels,
    select_items,
    stored_refs,
    unlabelled,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TABLE = """## Judge calibration list for Session 13

| Stage | Item | Judge | Verdict | Why it is wrong |
|---|---|---|---|---|
| v6, v7 | g012 | correctness | INCORRECT | ... |
| v8 | g001, g010 | invented content | INVENTED | ... |
| v9 | s02 (agent scenario) | correctness | INCORRECT | ... |

## Rate limits
| v1 | g999 | not part of the list |
"""


def test_the_calibration_list_is_read_with_every_stage_and_item() -> None:
    """Multi-stage rows and multi-item cells expand; the next section is ignored."""
    assert calibration_refs(TABLE) == [
        ItemRef("v6", "g012"),
        ItemRef("v7", "g012"),
        ItemRef("v8", "g001"),
        ItemRef("v8", "g010"),
        ItemRef("v9", "s02"),
    ]


def test_the_40_items_include_every_calibration_item_and_are_reproducible() -> None:
    """All of the real calibration list, then a seeded sample of golden rows."""
    calibration = calibration_refs((PROJECT_ROOT / "docs" / "evaluation.md").read_text("utf-8"))
    available = stored_refs(PROJECT_ROOT / "results")
    items = select_items(calibration, available)
    assert len(items) == 40 and len(set(items)) == 40
    assert set(calibration) <= set(items)
    assert items == select_items(calibration, available)  # the same 40 every time
    assert {r.stage for r in items if r not in calibration} <= {
        "v4", "v5", "v6", "v6a", "v7", "v8", "v9", "v10",
    }  # fmt: skip


def test_labels_are_appended_and_a_session_resumes(tmp_path: Path) -> None:
    """Labelled items are skipped next time; the label must be pass or fail."""
    path = tmp_path / "judge_labels.jsonl"
    items = [ItemRef("v7", "g012"), ItemRef("v9", "s02")]
    append_label(path, Label(stage="v7", item_id="g012", kind="golden", label="fail", note="x"))
    assert [x.item_id for x in load_labels(path)] == ["g012"]
    assert unlabelled(items, load_labels(path)) == [ItemRef("v9", "s02")]
    with pytest.raises(ValidationError):
        Label(stage="v7", item_id="g012", kind="golden", label="maybe")  # type: ignore[arg-type]

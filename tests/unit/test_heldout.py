"""The held-out set (Session 13): format, freezing, one run per stage, and no other use."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from rag_assistant.eval.heldout import (
    HELDOUT_FILE,
    LOCK_FILE,
    HeldoutError,
    file_sha256,
    freeze,
    load_heldout,
    run_dir,
    verify,
)
from rag_assistant.schemas import load_golden

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TAGS = {
    "agent_access", "chunking", "hallucination", "injection", "leak", "normal",
    "product_code", "query_region", "ranking", "stale",
}  # fmt: skip


def test_the_heldout_set_has_the_golden_format_and_covers_every_tag() -> None:
    """20 written questions (plus up to 5 of the owner's), unique ids, every failure tag."""
    items = load_golden(HELDOUT_FILE)
    golden = load_golden(PROJECT_ROOT / "data" / "golden" / "questions.jsonl")
    assert 20 <= len(items) <= 25
    assert len({i.id for i in items}) == len(items)
    assert not {i.id for i in items} & {g.id for g in golden}
    assert {i.failure_tag for i in items} >= TAGS
    assert not {i.question for i in items} & {g.question for g in golden}


def test_the_heldout_file_has_not_changed_since_it_was_frozen() -> None:
    """After scripts/freeze_heldout.py, any edit fails this test. Before it: format only."""
    if not LOCK_FILE.exists():
        pytest.skip("not frozen yet: the owner adds 5 questions, then runs freeze_heldout.py")
    assert verify()["sha256"] == file_sha256(HELDOUT_FILE)


def test_freeze_then_any_edit_is_refused(tmp_path: Path) -> None:
    """The lock records the hash; a changed file and a second freeze are refused."""
    data, lock = tmp_path / "heldout.jsonl", tmp_path / "heldout.lock"
    shutil.copy(HELDOUT_FILE, data)
    with pytest.raises(HeldoutError, match="not frozen"):
        load_heldout(data, lock)
    assert freeze(data, lock)["items"] == len(load_golden(data))
    assert len(load_heldout(data, lock)) == len(load_golden(data))
    with pytest.raises(HeldoutError, match="already exists"):
        freeze(data, lock)
    data.write_text(data.read_text(encoding="utf-8").replace("?", "?!", 1), encoding="utf-8")
    with pytest.raises(HeldoutError, match="changed after it was frozen"):
        load_heldout(data, lock)


def test_each_stage_runs_the_heldout_set_once(tmp_path: Path) -> None:
    """A second held-out run for the same stage is refused."""
    first = run_dir(tmp_path, "v9")
    first.mkdir(parents=True)
    with pytest.raises(HeldoutError, match="already run"):
        run_dir(tmp_path, "v9")
    assert run_dir(tmp_path, "v10") != first


def test_nothing_but_the_heldout_runner_reads_the_file() -> None:
    """The held-out file is named only by eval/heldout.py (and its tests and docs), so no
    tuning script or config can read it."""
    allowed = {
        PROJECT_ROOT / "src" / "rag_assistant" / "eval" / "heldout.py",
        PROJECT_ROOT / "scripts" / "freeze_heldout.py",
        Path(__file__).resolve(),
    }
    names = ("heldout.jsonl", "heldout.lock")
    offenders = [
        p
        for folder in ("src", "scripts", "configs")
        for p in (PROJECT_ROOT / folder).rglob("*")
        if p.is_file() and p.suffix in {".py", ".yaml", ".yml"} and p.resolve() not in allowed
        and any(n in p.read_text(encoding="utf-8", errors="ignore") for n in names)
    ]  # fmt: skip
    assert offenders == []


def test_the_hash_ignores_line_endings_but_not_content(tmp_path: Path) -> None:
    """A CRLF checkout (Windows) and an LF checkout (Linux CI) give the same hash."""
    lf, crlf, edited = tmp_path / "lf.jsonl", tmp_path / "crlf.jsonl", tmp_path / "e.jsonl"
    lf.write_bytes(b'{"a": 1}\n{"b": 2}\n')
    crlf.write_bytes(b'{"a": 1}\r\n{"b": 2}\r\n')
    edited.write_bytes(b'{"a": 1}\n{"b": 3}\n')
    assert file_sha256(lf) == file_sha256(crlf) != file_sha256(edited)

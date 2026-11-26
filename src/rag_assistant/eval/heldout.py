"""The held-out set (Session 13): 25 golden-format questions that are never used for tuning.

``data/golden/heldout.jsonl`` holds 20 questions written from the generated documents (one
or two per failure tag, none close to a golden question) plus 5 written by the project
owner. Three rules keep it honest:

1. **Frozen.** ``scripts/freeze_heldout.py`` (run once, after the owner's questions are
   in) writes ``data/golden/heldout.lock`` with the file's sha256. From then on a unit test
   fails if the file changes, and ``load_heldout`` refuses a file that doesn't match.
2. **Run once per stage.** ``evaluate.py --heldout`` refuses to run if
   ``results/heldout/<stage>/`` already exists, so the set can't be re-run after looking
   at its results.
3. **Not for tuning.** Only ``evaluate.py --heldout`` reads it (a test checks that no other
   script or config names the file).
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

from rag_assistant.config import PROJECT_ROOT
from rag_assistant.schemas import GoldenItem, load_golden

HELDOUT_FILE = PROJECT_ROOT / "data" / "golden" / "heldout.jsonl"
LOCK_FILE = PROJECT_ROOT / "data" / "golden" / "heldout.lock"
RESULTS_DIR = Path("heldout")  # under results/: results/heldout/<stage>/


class HeldoutError(RuntimeError):
    """The held-out set isn't frozen, has changed, or was already run for this stage."""


def file_sha256(path: Path) -> str:
    """The sha256 of ``path``'s bytes with line endings normalised to LF.

    Git may check the file out with CRLF on Windows and LF on Linux CI; the lock must
    match on both, while any change to the content still changes the hash.
    """
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def freeze(path: Path = HELDOUT_FILE, lock: Path = LOCK_FILE) -> dict:
    """Write the lock for ``path`` (refuses to overwrite an existing lock)."""
    if lock.exists():
        raise HeldoutError(f"{lock.name} already exists: the held-out set is frozen")
    items = load_golden(path)
    record = {"file": path.name, "sha256": file_sha256(path), "items": len(items),
              "frozen": date.today().isoformat()}  # fmt: skip
    lock.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8", newline="\n")
    return record


def verify(path: Path = HELDOUT_FILE, lock: Path = LOCK_FILE) -> dict:
    """The lock record, if ``path`` is frozen and unchanged; ``HeldoutError`` otherwise."""
    if not lock.exists():
        raise HeldoutError("the held-out set is not frozen yet (run scripts/freeze_heldout.py)")
    record = json.loads(lock.read_text(encoding="utf-8"))
    if file_sha256(path) != record["sha256"]:
        raise HeldoutError(f"{path.name} changed after it was frozen")
    return record


def load_heldout(path: Path = HELDOUT_FILE, lock: Path = LOCK_FILE) -> list[GoldenItem]:
    """The frozen held-out items (after checking the lock)."""
    verify(path, lock)
    return load_golden(path)


def run_dir(results_root: Path, stage_name: str) -> Path:
    """Where a stage's single held-out run goes; ``HeldoutError`` if it already exists."""
    out = results_root / RESULTS_DIR / stage_name
    if out.exists():
        raise HeldoutError(f"the held-out set was already run for {stage_name} ({out})")
    return out

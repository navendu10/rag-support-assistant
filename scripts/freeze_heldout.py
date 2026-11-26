"""Freeze the held-out set: write data/golden/heldout.lock with the file's sha256.

Usage:
    python scripts/freeze_heldout.py

Run it once, after adding your own questions to data/golden/heldout.jsonl. From then on
the unit tests fail if the file changes, and evaluate.py --heldout refuses a changed file.
An existing lock is never overwritten.
"""

from __future__ import annotations

import sys

from rag_assistant.eval.heldout import HELDOUT_FILE, LOCK_FILE, HeldoutError, freeze


def main() -> int:
    """Freeze the held-out file, or explain why not."""
    try:
        record = freeze()
    except HeldoutError as exc:
        print(f"Not frozen: {exc}")
        return 1
    print(
        f"Froze {HELDOUT_FILE.name}: {record['items']} questions, sha256 {record['sha256'][:16]}..."
    )
    print(f"Wrote {LOCK_FILE}. Commit both files; don't edit heldout.jsonl again.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

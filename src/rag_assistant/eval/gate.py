"""The CI retrieval gate (Session 13): recall@5 and retrieval leaks without an LLM.

``scripts/retrieval_gate.py`` runs this in GitHub Actions against a fresh Weaviate with
the generated data ingested as v7. Each golden question is searched with v7's **stored**
rewrite (``results/v7/results.jsonl``), as its user, with that role's Weaviate key, so
nothing calls Groq. The gate fails when:

- recall@5 is more than ``tolerance`` (2) questions below the committed baseline
  (``results/v7/retrieval_baseline.json``);
- any internal chunk reaches a customer's ranked results or candidates (a retrieval leak);
- a chunk can't find itself by its own vector (a broken vector index: Session 13 found 39
  of 156 v7 chunks missing from it after a Weaviate restart);
- any HTTP request was made to the LLM provider.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path

BASELINE_FILE = Path("results/v7/retrieval_baseline.json")
TOLERANCE = 2  # questions of recall@5 the gate allows below the baseline


@dataclass(frozen=True)
class GateResult:
    """What one gate run measured."""

    recall_hits: int
    questions: int
    leaks: list[str] = field(default_factory=list)  # "g019: pb-fraud-checks#0 for cust_us_01"
    index_misses: list[str] = field(default_factory=list)  # "Chunks_v7_public: 12/156 missing"
    llm_requests: int = 0
    misses: list[str] = field(default_factory=list)  # golden ids without a hit in the top 5

    def to_dict(self) -> dict:
        """JSON-ready."""
        return asdict(self)


def problems(result: GateResult, baseline: dict, tolerance: int = TOLERANCE) -> list[str]:
    """Why the gate fails (empty: it passes)."""
    found = []
    floor = int(baseline["recall_hits"]) - tolerance
    if result.recall_hits < floor:
        found.append(
            f"recall@5 {result.recall_hits}/{result.questions} is below the baseline "
            f"{baseline['recall_hits']}/{baseline['questions']} minus {tolerance}"
        )
    found += [f"retrieval leak: {leak}" for leak in result.leaks]
    found += [f"broken vector index: {miss}" for miss in result.index_misses]
    if result.llm_requests:
        found.append(f"{result.llm_requests} LLM requests were made (the gate must make none)")
    return found


def leaks_for(item_id: str, user_id: str, role: str, chunks: Sequence[object]) -> list[str]:
    """Internal chunks a customer received (agents may see them)."""
    if role != "customer":
        return []
    return [
        f"{item_id}: {c.doc_id}#{c.chunk_index} for {user_id}"  # type: ignore[attr-defined]
        for c in chunks
        if getattr(c, "audience", "public") == "internal"
    ]


def load_baseline(path: Path) -> dict:
    """The committed baseline."""
    return json.loads(path.read_text(encoding="utf-8"))


def baseline_record(result: GateResult, note: str) -> dict:
    """What ``--write-baseline`` commits."""
    return {"recall_hits": result.recall_hits, "questions": result.questions,
            "leaks": len(result.leaks), "misses": result.misses, "note": note}  # fmt: skip

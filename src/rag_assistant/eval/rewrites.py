"""Record and check query rewrites in evaluation runs (Session 6), with no LLM calls.

``RewriteChecker`` turns a pipeline's ``RewriteResult`` into the ``RewriteRecord``
stored in ``results.jsonl``, with flags for rewrites that invent a product, drop a code
or add a country (``query.rewrite.rewrite_flags``). ``--rescore`` re-runs the flags on
stored records, so changing a check never needs new LLM calls.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from rag_assistant.config import Settings
from rag_assistant.eval.metrics import ResultRow, RewriteRecord, first_expected_rank
from rag_assistant.query.rewrite import Catalogue, Rewrite, RewriteResult, rewrite_flags
from rag_assistant.retrieval.codes import CodeDetector
from rag_assistant.schemas import GoldenItem, load_catalogue


@dataclass(frozen=True)
class RewriteChecker:
    """The catalogue and code detector the rewrite checks need."""

    catalogue: Catalogue
    detector: CodeDetector

    @classmethod
    def from_manifest(cls, manifest_path: Path) -> RewriteChecker:
        """Build from ``manifest.json``'s product catalogue and product codes."""
        catalogue = Catalogue(tuple(load_catalogue(manifest_path)))
        return cls(catalogue, CodeDetector.from_manifest(manifest_path))

    @classmethod
    def from_settings(cls, settings: Settings) -> RewriteChecker:
        """Build from the configured generated-data directory."""
        generated = settings.paths.resolve(settings.paths.generated_dir)
        return cls.from_manifest(generated / "manifest.json")

    def flags(self, rewrite: Rewrite, item: GoldenItem) -> list[str]:
        """The rewrite checks for ``item``'s question and history."""
        return rewrite_flags(
            rewrite,
            item.question,
            item.history,
            self.catalogue,
            self.detector.find(item.question),
            self.detector.find(rewrite.search_query),
        )

    def record(self, result: RewriteResult, item: GoldenItem) -> RewriteRecord:
        """The stored form of a rewrite, with its flags."""
        rewrite = result.rewrite
        return RewriteRecord(
            standalone_question=rewrite.standalone_question,
            search_query=rewrite.search_query,
            product_codes=list(rewrite.product_codes),
            product_category=rewrite.product_category,
            fallback=result.fallback,
            attempts=result.attempts,
            error=result.error,
            flags=self.flags(rewrite, item),
        )

    def reflag(self, record: RewriteRecord, item: GoldenItem) -> RewriteRecord:
        """``record`` with its flags recomputed by the current checks."""
        rewrite = Rewrite(
            standalone_question=record.standalone_question,
            search_query=record.search_query,
            product_codes=record.product_codes,
            product_category=record.product_category,
        )
        return record.model_copy(update={"flags": self.flags(rewrite, item)})


def _cell(text: object) -> str:
    """Make a value safe for a Markdown table cell."""
    return str(text).replace("|", "\\|").replace("\n", " ")


def short_history(item: GoldenItem, width: int = 60) -> str:
    """The conversation's user turns, joined and cut to ``width`` characters ("-" if none)."""
    said = " / ".join(t.content for t in item.history if t.role == "user")
    if not said:
        return "-"
    return said if len(said) <= width else said[: width - 1].rstrip() + "…"


def rewrites_table(entries: Sequence[tuple[GoldenItem, RewriteRecord]]) -> str:
    """Markdown: question, short history and the rewrite, one row per (item, rewrite)."""
    lines = [
        "| id | tag | question | history (user turns) | standalone_question | search_query "
        "| category | codes | flags |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for item, r in entries:
        flags = [
            *(["FALLBACK"] if r.fallback else []),
            *r.flags,
        ]
        lines.append(
            f"| {item.id} | {item.failure_tag} | {_cell(item.question)} | "
            f"{_cell(short_history(item))} | {_cell(r.standalone_question)} | "
            f"{_cell(r.search_query)} | {r.product_category or '-'} | "
            f"{', '.join(r.product_codes) or '-'} | "
            f"{_cell(', '.join(flags) or '-')} |"
        )
    return "\n".join(lines)


def stored_entries(
    rows: Sequence[ResultRow], items: Sequence[GoldenItem]
) -> list[tuple[GoldenItem, RewriteRecord]]:
    """(item, stored rewrite) pairs for every row of a run that has a rewrite."""
    by_id = {i.id: i for i in items}
    return [(by_id[r.id], r.rewrite) for r in rows if r.rewrite is not None and r.id in by_id]


# ------------------------------------------------- retrieval-only comparison


@dataclass(frozen=True)
class RetrievalScore:
    """Ranking metrics of one stage's top 5 over the golden items with expected docs."""

    recall: int
    hit1: int
    mrr: float
    total: int

    def cell(self) -> str:
        """``recall · hit@1 · MRR`` for a Markdown table."""
        return f"{self.recall}/{self.total} · {self.hit1} · {self.mrr:.2f}"


def score_retrieval(
    expected: dict[str, Sequence[str]], retrieved: dict[str, Sequence[str]]
) -> RetrievalScore:
    """Recall@5, hit@1 and MRR of ``retrieved`` (id -> top-5 doc_ids) over ``expected``."""
    ids = [i for i, docs in expected.items() if docs]
    ranks = [first_expected_rank(expected[i], retrieved.get(i, [])) for i in ids]
    found = [r for r in ranks if r is not None]
    return RetrievalScore(
        recall=len(found),
        hit1=sum(1 for r in found if r == 1),
        mrr=round(sum(1 / r for r in found) / len(ids), 3) if ids else 0.0,
        total=len(ids),
    )


def rank_changes(
    expected: dict[str, Sequence[str]],
    before: dict[str, Sequence[str]],
    after: dict[str, Sequence[str]],
) -> list[tuple[str, int | None, int | None]]:
    """(id, rank before, rank after) for every item whose first expected rank changed."""
    changes = []
    for item_id, docs in expected.items():
        if not docs:
            continue
        old = first_expected_rank(docs, before.get(item_id, []))
        new = first_expected_rank(docs, after.get(item_id, []))
        if old != new:
            changes.append((item_id, old, new))
    return changes

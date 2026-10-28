"""Run a stage over the golden set and write results.jsonl, summary.json and report.md."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from rag_assistant.config import Settings, StageConfig, load_prompts
from rag_assistant.eval.judge import Judge, internal_reference, needs_leak_judge
from rag_assistant.eval.metrics import (
    ResultRow,
    RetrievedRef,
    check_item,
    summarise_by_tag,
    summarise_rows,
)
from rag_assistant.eval.report import render_report
from rag_assistant.llm import make_chat_model
from rag_assistant.pipeline import Pipeline
from rag_assistant.schemas import GoldenItem, User, load_golden, load_users

SUBSET_DIR = "subset"


@dataclass(frozen=True)
class RunOutput:
    """Where a run was written and its headline numbers."""

    out_dir: Path
    summary: dict[str, object]
    rows: list[ResultRow]


def select_items(
    items: Sequence[GoldenItem], tag: str | None = None, limit: int | None = None
) -> list[GoldenItem]:
    """Filter by failure tag, then keep the first ``limit`` items."""
    chosen = [i for i in items if tag is None or i.failure_tag == tag]
    return chosen[:limit] if limit is not None else chosen


def output_dir(settings: Settings, stage: StageConfig, filtered: bool) -> Path:
    """Full runs go to results/<stage>/; filtered runs to results/<stage>/subset/."""
    base = settings.paths.resolve(settings.paths.results_dir) / stage.name
    return base / SUBSET_DIR if filtered else base


def evaluate_item(
    item: GoldenItem,
    user: User,
    pipeline: Pipeline,
    judge: Judge,
    recall_k: int,
    internal: str = "",
) -> ResultRow:
    """Answer one golden question and score it (``internal``: text for the leak judge)."""
    answer = pipeline.answer(item.question, user, item.history, retrieve_k=recall_k)
    retrieved_ids = [c.doc_id for c in answer.retrieved[:recall_k]]
    context_audiences = [c.audience for c in answer.context]
    candidate_ids = [c.doc_id for c in answer.candidates] if pipeline.stage.rerank else None
    return ResultRow(
        id=item.id,
        question=item.question,
        user_id=user.user_id,
        user_role=user.role,
        user_region=user.region,
        failure_tag=item.failure_tag,
        topic=item.topic,
        history_turns=len(item.history),
        expected_doc_ids=item.expected_doc_ids,
        must_not_contain=item.must_not_contain,
        expect_handoff=item.expect_handoff,
        retrieved=[
            RetrievedRef(
                doc_id=c.doc_id,
                chunk_index=c.chunk_index,
                audience=c.audience,
                region=c.region,
                score=round(c.score, 4),
                section=c.section or None,
                page=c.page,
                rerank_score=None if c.rerank_score is None else round(c.rerank_score, 4),
            )
            for c in answer.retrieved[:recall_k]
        ],
        context_doc_ids=[c.doc_id for c in answer.context],
        context_audiences=context_audiences,
        answer=answer.text,
        checks=check_item(item, user, retrieved_ids, context_audiences, answer.text, candidate_ids),
        correctness=judge.correctness(item, answer.text),
        handoff=judge.handoff(item, answer.text) if item.expect_handoff else None,
        leak_judgement=(
            judge.leak(item.question, answer.text, internal)
            if needs_leak_judge(item.failure_tag, user.role)
            else None
        ),
        latency_ms=answer.timings_ms,
        candidate_doc_ids=candidate_ids,
    )


def write_outputs(out_dir: Path, meta: dict[str, object], rows: list[ResultRow]) -> dict:
    """Write results.jsonl, summary.json and report.md; return the summary."""
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "results.jsonl").open("w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(row.model_dump_json() + "\n")
    summary = summarise_rows(rows)
    by_tag = summarise_by_tag(rows)
    payload = {"meta": meta, "overall": summary, "by_tag": by_tag}
    (out_dir / "summary.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    (out_dir / "report.md").write_text(
        render_report(meta, summary, by_tag, rows), encoding="utf-8", newline="\n"
    )
    return summary


def answer_items(
    stage: StageConfig,
    settings: Settings,
    items: Sequence[GoldenItem],
    progress: Callable[[str], None] = print,
) -> list[ResultRow]:
    """Answer and score ``items`` with the stage's pipeline, printing one line each."""
    users = {u.user_id: u for u in load_users(settings.paths.resolve(settings.paths.users_file))}
    judge = Judge(make_chat_model(settings, settings.llm.judge_model), load_prompts())
    recall_k = settings.eval.recall_k
    internal = internal_reference(settings.paths.resolve(settings.paths.generated_dir))
    rows: list[ResultRow] = []
    with Pipeline.from_stage(stage, settings) as pipeline:
        for n, item in enumerate(items, start=1):
            row = evaluate_item(item, users[item.user_id], pipeline, judge, recall_k, internal)
            rows.append(row)
            hit = {True: "hit", False: "MISS", None: "n/a"}[row.checks.recall_hit]
            progress(
                f"[{n:>2}/{len(items)}] {item.id} {item.failure_tag:<13} recall={hit:<4} "
                f"{row.correctness.verdict or 'JUDGE-ERROR'}"
            )
    return rows


def run_evaluation(
    stage: StageConfig,
    settings: Settings,
    tag: str | None = None,
    limit: int | None = None,
    progress: Callable[[str], None] = print,
) -> RunOutput:
    """Evaluate ``stage`` on the golden set (optionally filtered) and write the outputs."""
    items = select_items(
        load_golden(settings.paths.resolve(settings.paths.golden_file)), tag, limit
    )
    rows = answer_items(stage, settings, items, progress)
    filters = ", ".join(
        part for part in (f"tag={tag}" if tag else "", f"limit={limit}" if limit else "") if part
    )
    meta = {
        "stage": stage.name,
        "date": date.today().isoformat(),
        "filters": filters,
        "generation_model": settings.llm.generation_model,
        "judge_model": settings.llm.judge_model,
        "reasoning_effort": settings.llm.reasoning_effort,
        "top_k": stage.top_k,
        "recall_k": settings.eval.recall_k,
        **({"rerank_model": stage.rerank_model, "fetch_k": stage.fetch_k} if stage.rerank else {}),
    }
    out_dir = output_dir(settings, stage, filtered=bool(filters))
    summary = write_outputs(out_dir, meta, rows)
    return RunOutput(out_dir, summary, rows)


def load_rows(out_dir: Path) -> list[ResultRow]:
    """Read the stored ``results.jsonl`` of a run."""
    return [
        ResultRow.model_validate_json(line)
        for line in (out_dir / "results.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def merge_rows(
    items: Sequence[GoldenItem], stored: Sequence[ResultRow], new: Sequence[ResultRow]
) -> list[ResultRow]:
    """Stored and newly answered rows in golden-set order (rows for removed items drop out)."""
    by_id = {row.id: row for row in [*stored, *new]}
    return [by_id[item.id] for item in items if item.id in by_id]


def missing_items(items: Sequence[GoldenItem], stored: Sequence[ResultRow]) -> list[GoldenItem]:
    """Golden items that have no row in ``stored`` yet."""
    done = {row.id for row in stored}
    return [item for item in items if item.id not in done]


def rescore_row(row: ResultRow, item: GoldenItem, user: User) -> ResultRow:
    """Recompute the string and metadata checks for a stored row.

    Answers, retrieval, judge verdicts and latency are kept as recorded; only the
    checks (and the item's current ``must_not_contain``) are refreshed.
    """
    checks = check_item(
        item,
        user,
        [r.doc_id for r in row.retrieved],
        row.context_audiences,
        row.answer,
        row.candidate_doc_ids,
    )
    return row.model_copy(update={"checks": checks, "must_not_contain": item.must_not_contain})


def _rescored_full_run(
    stage: StageConfig, settings: Settings
) -> tuple[Path, list[GoldenItem], list[ResultRow], dict]:
    """Load a stage's full run, rescore every stored row and return it with its metadata."""
    out_dir = output_dir(settings, stage, filtered=False)
    items = load_golden(settings.paths.resolve(settings.paths.golden_file))
    by_id = {i.id: i for i in items}
    users = {u.user_id: u for u in load_users(settings.paths.resolve(settings.paths.users_file))}
    stored = [
        rescore_row(r, by_id[r.id], users[r.user_id]) for r in load_rows(out_dir) if r.id in by_id
    ]
    meta = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))["meta"]
    return out_dir, items, stored, meta


def rescore(stage: StageConfig, settings: Settings) -> RunOutput:
    """Re-apply the current metric code to an existing full run, with no LLM calls."""
    out_dir, _, rows, meta = _rescored_full_run(stage, settings)
    meta["rescored"] = date.today().isoformat()
    summary = write_outputs(out_dir, meta, rows)
    return RunOutput(out_dir, summary, rows)


def evaluate_missing(
    stage: StageConfig, settings: Settings, progress: Callable[[str], None] = print
) -> RunOutput:
    """Answer only the golden items a stored full run lacks, then rescore and merge.

    Stored rows keep their real answers and latencies, so adding questions does not
    replace them with cache-hit timings.
    """
    out_dir, items, stored, meta = _rescored_full_run(stage, settings)
    todo = missing_items(items, stored)
    new = answer_items(stage, settings, todo, progress) if todo else []
    rows = merge_rows(items, stored, new)
    if todo:
        meta["extended"] = f"{date.today().isoformat()}: added {', '.join(i.id for i in todo)}"
    summary = write_outputs(out_dir, meta, rows)
    return RunOutput(out_dir, summary, rows)


def add_leak_judgements(
    stage: StageConfig, settings: Settings, progress: Callable[[str], None] = print
) -> RunOutput:
    """Run the leak judge on a stored full run's customer leak answers, then rescore.

    Stored answers, verdicts and latencies are kept; only rows that need the judge and
    don't have its verdict yet make an LLM call (small model).
    """
    out_dir, _, rows, meta = _rescored_full_run(stage, settings)
    judge = Judge(make_chat_model(settings, settings.llm.judge_model), load_prompts())
    internal = internal_reference(settings.paths.resolve(settings.paths.generated_dir))
    updated = []
    for row in rows:
        if needs_leak_judge(row.failure_tag, row.user_role) and row.leak_judgement is None:
            verdict = judge.leak(row.question, row.answer, internal)
            progress(f"{row.id} leak judge: {verdict.verdict or 'JUDGE-ERROR'} - {verdict.reason}")
            row = row.model_copy(update={"leak_judgement": verdict})
        updated.append(row)
    meta["leak_judged"] = date.today().isoformat()
    summary = write_outputs(out_dir, meta, updated)
    return RunOutput(out_dir, summary, updated)

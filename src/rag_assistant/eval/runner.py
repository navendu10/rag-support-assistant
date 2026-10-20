"""Run a stage over the golden set and write results.jsonl, summary.json and report.md."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from rag_assistant.config import Settings, StageConfig, load_prompts
from rag_assistant.eval.judge import Judge
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
    item: GoldenItem, user: User, pipeline: Pipeline, judge: Judge, recall_k: int
) -> ResultRow:
    """Answer one golden question and score it."""
    answer = pipeline.answer(item.question, user, item.history, retrieve_k=recall_k)
    retrieved_ids = [c.doc_id for c in answer.retrieved[:recall_k]]
    context_audiences = [c.audience for c in answer.context]
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
            )
            for c in answer.retrieved[:recall_k]
        ],
        context_doc_ids=[c.doc_id for c in answer.context],
        context_audiences=context_audiences,
        answer=answer.text,
        checks=check_item(item, user, retrieved_ids, context_audiences, answer.text),
        correctness=judge.correctness(item, answer.text),
        handoff=judge.handoff(item, answer.text) if item.expect_handoff else None,
        latency_ms=answer.timings_ms,
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
    users = {u.user_id: u for u in load_users(settings.paths.resolve(settings.paths.users_file))}
    judge = Judge(make_chat_model(settings, settings.llm.judge_model), load_prompts())
    recall_k = settings.eval.recall_k
    rows: list[ResultRow] = []
    with Pipeline.from_stage(stage, settings) as pipeline:
        for n, item in enumerate(items, start=1):
            row = evaluate_item(item, users[item.user_id], pipeline, judge, recall_k)
            rows.append(row)
            hit = {True: "hit", False: "MISS", None: "n/a"}[row.checks.recall_hit]
            progress(
                f"[{n:>2}/{len(items)}] {item.id} {item.failure_tag:<13} recall={hit:<4} "
                f"{row.correctness.verdict or 'JUDGE-ERROR'}"
            )
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
        "recall_k": recall_k,
    }
    out_dir = output_dir(settings, stage, filtered=bool(filters))
    summary = write_outputs(out_dir, meta, rows)
    return RunOutput(out_dir, summary, rows)


def rescore_row(row: ResultRow, item: GoldenItem, user: User) -> ResultRow:
    """Recompute the string and metadata checks for a stored row.

    Answers, retrieval, judge verdicts and latency are kept as recorded; only the
    checks (and the item's current ``must_not_contain``) are refreshed.
    """
    checks = check_item(
        item, user, [r.doc_id for r in row.retrieved], row.context_audiences, row.answer
    )
    return row.model_copy(update={"checks": checks, "must_not_contain": item.must_not_contain})


def rescore(stage: StageConfig, settings: Settings) -> RunOutput:
    """Re-apply the current metric code to an existing full run, with no LLM calls."""
    out_dir = output_dir(settings, stage, filtered=False)
    stored = [
        ResultRow.model_validate_json(line)
        for line in (out_dir / "results.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    items = {i.id: i for i in load_golden(settings.paths.resolve(settings.paths.golden_file))}
    users = {u.user_id: u for u in load_users(settings.paths.resolve(settings.paths.users_file))}
    rows = [rescore_row(r, items[r.id], users[r.user_id]) for r in stored]
    meta = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))["meta"]
    meta["rescored"] = date.today().isoformat()
    summary = write_outputs(out_dir, meta, rows)
    return RunOutput(out_dir, summary, rows)

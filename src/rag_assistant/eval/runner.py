"""Run a stage over the golden set and write results.jsonl, summary.json and report.md."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from rag_assistant.config import Settings, StageConfig, load_prompts
from rag_assistant.eval.contexts import chunk_lookup, stored_context
from rag_assistant.eval.judge import Judge, internal_reference, needs_leak_judge
from rag_assistant.eval.metrics import (
    GroundedRecord,
    Judgement,
    PathRecord,
    ResultRow,
    RetrievedRef,
    check_item,
    invented_input,
    summarise_by_tag,
    summarise_rows,
)
from rag_assistant.eval.report import render_report
from rag_assistant.eval.rewrites import RewriteChecker
from rag_assistant.generation.grounded import format_sources
from rag_assistant.graph.handoff import MemoryTicketStore
from rag_assistant.llm import make_chat_model
from rag_assistant.pipeline import Answer, Pipeline
from rag_assistant.schemas import GoldenItem, User, load_golden, load_manifest, load_users

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


def internal_doc_ids(settings: Settings) -> frozenset[str]:
    """Doc ids with ``audience=internal`` in the manifest (for agent internal access)."""
    manifest = settings.paths.resolve(settings.paths.generated_dir) / "manifest.json"
    return frozenset(d.doc_id for d in load_manifest(manifest) if d.audience == "internal")


# The verdict recorded, without an LLM call, for an answer that is only our fixed text.
FIXED_MESSAGE_VERDICT = Judgement(
    verdict="SUPPORTED", reason="fixed handoff message only; nothing model-written to judge"
)


def grounded_record(answer: Answer) -> GroundedRecord | None:
    """The stored form of the grounded reply (None for naive stages)."""
    g = answer.grounded
    if g is None:
        return None
    return GroundedRecord(
        found_in_sources=g.found_in_sources,
        citations=list(g.citations),
        valid=g.valid,
        attempts=g.attempts,
        error=g.error,
        cited_doc_ids=[s.doc_id for s in answer.sources],
    )


def path_record(answer: Answer) -> PathRecord | None:
    """The stored form of the corrective flow's route (None without the flow)."""
    p = answer.path
    if p is None:
        return None
    return PathRecord(
        grade=p.grade,
        best_score=None if p.best_score is None else round(p.best_score, 4),
        retry_used=p.retry_used,
        retry_best_score=None if p.retry_best_score is None else round(p.retry_best_score, 4),
        retry_kept=p.retry_kept,
        handoff_reason=p.handoff_reason,
        ticket_created=p.ticket_id is not None,
        retry_trigger=p.retry_trigger,
        retry_changed_outcome=p.retry_changed_outcome,
    )


def judge_invented(judge: Judge, row: ResultRow, sources: str) -> Judgement:
    """The invented-content verdict for ``row`` (no LLM call for a fixed message alone)."""
    text = invented_input(row)
    if text is None:
        return FIXED_MESSAGE_VERDICT
    return judge.invented(row.question, text, sources)


def evaluate_item(
    item: GoldenItem,
    user: User,
    pipeline: Pipeline,
    judge: Judge,
    recall_k: int,
    internal: str = "",
    checker: RewriteChecker | None = None,
    internal_ids: frozenset[str] = frozenset(),
) -> ResultRow:
    """Answer one golden question and score it.

    ``internal`` is the text for the leak judge; ``checker`` records and flags the
    query rewrite (needed when the stage rewrites). The handoff judge runs on every
    question (Session 8: false handoffs), and grounding stages also get the
    invented-content judge on every question.
    """
    answer = pipeline.answer(item.question, user, item.history, retrieve_k=recall_k)
    rewrite = None
    if answer.rewrite is not None:
        if checker is None:
            raise ValueError("a rewriting stage needs a RewriteChecker")
        rewrite = checker.record(answer.rewrite, item)
    retrieved_ids = [c.doc_id for c in answer.retrieved[:recall_k]]
    context_audiences = [c.audience for c in answer.context]
    candidate_ids = [c.doc_id for c in answer.candidates] if pipeline.stage.rerank else None
    row = ResultRow(
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
            for c in answer.retrieved[: max(recall_k, len(answer.context))]
        ],
        context_doc_ids=[c.doc_id for c in answer.context],
        context_audiences=context_audiences,
        answer=answer.text,
        checks=check_item(
            item,
            user,
            retrieved_ids,
            context_audiences,
            answer.text,
            candidate_ids,
            [c.doc_id for c in answer.context],
            internal_ids,
        ),
        correctness=judge.correctness(item, answer.text),
        handoff=judge.handoff(item, answer.text),
        leak_judgement=(
            judge.leak(item.question, answer.text, internal)
            if needs_leak_judge(item.failure_tag, user.role)
            else None
        ),
        latency_ms=answer.timings_ms,
        candidate_doc_ids=candidate_ids,
        rewrite=rewrite,
        region_filter=None if answer.region_filter is None else list(answer.region_filter),
        security_events=answer.security_events if pipeline.stage.access_control else None,
        grounded=grounded_record(answer),
        path=path_record(answer),
        model_text=answer.model_text if answer.model_text != answer.text else None,
        fixed_message=answer.grounded is not None and answer.model_text is None,
    )
    if pipeline.stage.grounding:
        row = row.model_copy(
            update={"invented": judge_invented(judge, row, format_sources(answer.context))}
        )
    return row


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
    checker = RewriteChecker.from_settings(settings) if stage.query_rewrite else None
    internal_ids = internal_doc_ids(settings)
    rows: list[ResultRow] = []
    # Evaluation never writes tickets to data/tickets/: rows record that one was created.
    with Pipeline.from_stage(stage, settings, tickets=MemoryTicketStore()) as pipeline:
        for n, item in enumerate(items, start=1):
            row = evaluate_item(
                item,
                users[item.user_id],
                pipeline,
                judge,
                recall_k,
                internal,
                checker,
                internal_ids,
            )
            rows.append(row)
            hit = {True: "hit", False: "MISS", None: "n/a"}[row.checks.recall_hit]
            fallback = " REWRITE-FALLBACK" if row.rewrite and row.rewrite.fallback else ""
            progress(
                f"[{n:>2}/{len(items)}] {item.id} {item.failure_tag:<13} recall={hit:<4} "
                f"{row.correctness.verdict or 'JUDGE-ERROR'}{fallback}"
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
        **(
            {
                "rewrite_model": settings.llm.small_model,
                "history_turns": settings.query.history_turns,
            }
            if stage.query_rewrite
            else {}
        ),
        **({"region_filter": "user profile region + ALL"} if stage.region_filter else {}),
        **(
            {"access_control": "per-audience collections; Weaviate key chosen by user role"}
            if stage.access_control
            else {}
        ),
        **(
            {"grounding": "role-aware grounded prompt, cited structured output"}
            if stage.grounding
            else {}
        ),
        **(
            {
                "corrective_flow": "LangGraph: grade, broader retry, answer or handoff",
                "min_score": stage.min_score,
                "retry_fetch_k": stage.retry_fetch_k,
            }
            if stage.corrective_flow
            else {}
        ),
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


def rescore_row(
    row: ResultRow,
    item: GoldenItem,
    user: User,
    checker: RewriteChecker | None = None,
    internal_ids: frozenset[str] = frozenset(),
    recall_k: int = 5,
) -> ResultRow:
    """Recompute the string and metadata checks for a stored row.

    Ranking metrics read the first ``recall_k`` stored chunks (rows may store more: the
    whole context of a wider retry).

    Answers, retrieval, judge verdicts and latency are kept as recorded; only the
    checks (and the item's current ``must_not_contain``) are refreshed, plus the
    rewrite flags when there is a stored rewrite and a ``checker``.
    """
    checks = check_item(
        item,
        user,
        [r.doc_id for r in row.retrieved[:recall_k]],
        row.context_audiences,
        row.answer,
        row.candidate_doc_ids,
        row.context_doc_ids,
        internal_ids,
    )
    update: dict[str, object] = {"checks": checks, "must_not_contain": item.must_not_contain}
    if row.rewrite is not None and checker is not None:
        update["rewrite"] = checker.reflag(row.rewrite, item)
    return row.model_copy(update=update)


def _rescored_full_run(
    stage: StageConfig, settings: Settings
) -> tuple[Path, list[GoldenItem], list[ResultRow], dict]:
    """Load a stage's full run, rescore every stored row and return it with its metadata."""
    out_dir = output_dir(settings, stage, filtered=False)
    items = load_golden(settings.paths.resolve(settings.paths.golden_file))
    by_id = {i.id: i for i in items}
    users = {u.user_id: u for u in load_users(settings.paths.resolve(settings.paths.users_file))}
    checker = RewriteChecker.from_settings(settings) if stage.query_rewrite else None
    internal_ids = internal_doc_ids(settings)
    stored = [
        rescore_row(r, by_id[r.id], users[r.user_id], checker, internal_ids, settings.eval.recall_k)
        for r in load_rows(out_dir)
        if r.id in by_id
    ]
    meta = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))["meta"]
    return out_dir, items, stored, meta


def rescore(stage: StageConfig, settings: Settings) -> RunOutput:
    """Re-apply the current metric code to an existing full run, with no LLM calls."""
    out_dir, _, rows, meta = _rescored_full_run(stage, settings)
    meta["rescored"] = date.today().isoformat()
    summary = write_outputs(out_dir, meta, rows)
    return RunOutput(out_dir, summary, rows)


def extension_note(previous: object, added: Sequence[str], today: date | None = None) -> str:
    """``meta.extended``: earlier notes kept, today's added ids appended after "; "."""
    note = f"{(today or date.today()).isoformat()}: added {', '.join(added)}"
    return f"{previous}; {note}" if previous else note


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
        meta["extended"] = extension_note(meta.get("extended"), [i.id for i in todo])
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


def add_handoff_judgements(
    stage: StageConfig, settings: Settings, progress: Callable[[str], None] = print
) -> RunOutput:
    """Run the handoff judge on stored answers that lack a verdict (false handoffs).

    Before Session 8 the judge ran only on ``expect_handoff`` questions; this back-fills
    the answerable ones from their stored answers (small model). Nothing is regenerated.
    """
    out_dir, items, rows, meta = _rescored_full_run(stage, settings)
    by_id = {i.id: i for i in items}
    judge = Judge(make_chat_model(settings, settings.llm.judge_model), load_prompts())
    updated = []
    for row in rows:
        if row.handoff is None:
            verdict = judge.handoff(by_id[row.id], row.answer)
            progress(f"{row.id} handoff judge: {verdict.verdict or 'JUDGE-ERROR'}")
            row = row.model_copy(update={"handoff": verdict})
        updated.append(row)
    meta["handoff_judged"] = date.today().isoformat()
    summary = write_outputs(out_dir, meta, updated)
    return RunOutput(out_dir, summary, updated)


def add_invented_judgements(
    stage: StageConfig, settings: Settings, progress: Callable[[str], None] = print
) -> RunOutput:
    """Run the invented-content judge on stored answers and their rebuilt contexts.

    Context texts are rebuilt offline (``eval/contexts.py``) and checked against the
    stored chunk references; no Weaviate key is used and no answer is regenerated.
    Rows that already have a verdict are kept; if the daily quota runs out, re-running
    replays the finished calls from the LLM cache.
    """
    out_dir, _, rows, meta = _rescored_full_run(stage, settings)
    lookup = chunk_lookup(stage, settings.paths.resolve(settings.paths.generated_dir))
    judge = Judge(make_chat_model(settings, settings.llm.judge_model), load_prompts())
    updated = []
    for row in rows:
        if row.invented is None:
            sources = format_sources(stored_context(row, lookup))
            verdict = judge_invented(judge, row, sources)
            progress(
                f"{row.id} invented judge: {verdict.verdict or 'JUDGE-ERROR'} - {verdict.reason}"
            )
            row = row.model_copy(update={"invented": verdict})
        updated.append(row)
    meta["invented_judged"] = date.today().isoformat()
    summary = write_outputs(out_dir, meta, updated)
    return RunOutput(out_dir, summary, updated)


def evaluate_ids(
    stage: StageConfig,
    settings: Settings,
    ids: Sequence[str],
    progress: Callable[[str], None] = print,
) -> RunOutput:
    """Re-answer only the golden items in ``ids`` and merge them into the stored full run.

    For a change that can only affect known questions (Session 8: the not-found retry
    only touches questions that handed off). Every other stored row keeps its answer,
    verdicts and latency. ``meta.rerun`` records which ids were re-answered and when.
    """
    out_dir, items, stored, meta = _rescored_full_run(stage, settings)
    unknown = sorted(set(ids) - {i.id for i in items})
    if unknown:
        raise KeyError(f"Unknown golden id(s): {', '.join(unknown)}")
    todo = [i for i in items if i.id in set(ids)]
    new = answer_items(stage, settings, todo, progress)
    rows = merge_rows(items, stored, new)
    note = f"{date.today().isoformat()}: re-answered {', '.join(i.id for i in todo)}"
    meta["rerun"] = f"{meta['rerun']}; {note}" if meta.get("rerun") else note
    summary = write_outputs(out_dir, meta, rows)
    return RunOutput(out_dir, summary, rows)

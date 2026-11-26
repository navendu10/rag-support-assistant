"""RAGAS metrics on stored golden answers (Session 13 Part B). No new answers are made.

Usage:
    python scripts/ragas_eval.py --stages v0,v7,v10

For each stage, every stored golden row (results/<stage>/results.jsonl) is scored with
faithfulness and answer relevancy (llm.ragas_model, FastEmbed embeddings) and the
non-LLM context precision and recall against the expected documents' chunks. Writes
results/<stage>/ragas.jsonl and results/ragas.md (per stage and per failure tag). Rows
already in ragas.jsonl are kept, so an interrupted run resumes where it stopped.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from rag_assistant.config import get_settings, load_stage
from rag_assistant.embeddings import Embedder
from rag_assistant.eval.contexts import chunk_lookup, stored_context
from rag_assistant.eval.ragas_eval import (
    METRICS,
    FastEmbedEmbeddings,
    RagasRow,
    make_scorers,
    score_row,
    summarise,
)
from rag_assistant.eval.runner import load_rows, output_dir
from rag_assistant.llm import make_chat_model
from rag_assistant.schemas import load_golden


def parse_args() -> argparse.Namespace:
    """Parse command-line options."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stages", default="v0,v7,v10", help="comma-separated stages")
    return parser.parse_args()


def _stored(path: Path, model: str) -> dict[str, RagasRow]:
    """Rows already scored for a stage with ``model`` (rows from another model, or from
    before rows recorded their model, are dropped so one model scores every row)."""
    if not path.is_file():
        return {}
    data = [json.loads(x) for x in path.read_text("utf-8").splitlines() if x.strip()]
    rows = [RagasRow(**d) for d in data if d.get("model") == model]
    if len(rows) != len(data):
        kept = "".join(json.dumps(r.to_dict()) + "\n" for r in rows)
        path.write_text(kept, encoding="utf-8", newline="\n")
    return {r.item_id: r for r in rows}


def _cell(value: float | None) -> str:
    """A table cell."""
    return "-" if value is None else f"{value:.2f}"


def render(summaries: dict[str, dict], counts: dict[str, int], model: str) -> str:
    """results/ragas.md: per stage, then per failure tag."""
    stages = list(summaries)
    head = "| Stage | n | Faithfulness | Answer relevancy | Context precision | Context recall |"
    lines = [
        "# RAGAS: stored golden answers",
        "",
        f"- Date: {date.today().isoformat()}. Faithfulness and answer relevancy: Groq "
        f"`{model}` (thinking off; one generated question per answer), FastEmbed embeddings. "
        "Context precision and recall: RAGAS's non-LLM variants against the expected "
        "documents' chunks.",
        "- `-`: not applicable (no context, or no expected documents).",
        "",
        head,
        "|---|---|---|---|---|---|",
    ]
    for stage in stages:
        s = summaries[stage]["all"]
        lines.append(
            f"| {stage} | {counts[stage]} | " + " | ".join(_cell(s[m]) for m in METRICS) + " |"
        )
    tags = sorted({t for s in summaries.values() for t in s if t != "all"})
    for metric in METRICS:
        lines += ["", f"## {metric.replace('_', ' ').capitalize()} by failure tag", "",
                  "| Failure tag | " + " | ".join(stages) + " |",
                  "|---|" + "---|" * len(stages)]  # fmt: skip
        for tag in tags:
            cells = " | ".join(_cell(summaries[s].get(tag, {}).get(metric)) for s in stages)
            lines.append(f"| {tag} | {cells} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    """Score each stage and write the tables."""
    args = parse_args()
    settings = get_settings()
    items = {i.id: i for i in load_golden(settings.paths.resolve(settings.paths.golden_file))}
    generated = settings.paths.resolve(settings.paths.generated_dir)
    scorers = make_scorers(
        make_chat_model(settings, settings.llm.ragas_model, settings.llm.ragas_reasoning_effort),
        FastEmbedEmbeddings(Embedder(settings.embedding)),
    )
    summaries, counts = {}, {}
    for name in [s.strip() for s in args.stages.split(",") if s.strip()]:
        stage = load_stage(name)
        lookup = chunk_lookup(stage, generated)
        by_doc: dict[str, list[str]] = {}
        for (doc_id, _), chunk in sorted(lookup.items()):
            by_doc.setdefault(doc_id, []).append(chunk.text)
        out = output_dir(settings, stage, False) / "ragas.jsonl"
        done = _stored(out, scorers.model)
        rows = []
        for row in load_rows(output_dir(settings, stage, False)):
            if row.id in done:
                rows.append(done[row.id])
                continue
            item = items[row.id]
            try:
                contexts = [c.text for c in stored_context(row, lookup)]
            except Exception:  # noqa: BLE001 - a context that can't be rebuilt is skipped
                contexts = []
            reference = [t for d in item.expected_doc_ids for t in by_doc.get(d, [])]
            result = score_row(scorers, row.id, row.failure_tag, row.question,
                               row.model_text or row.answer, contexts, reference)  # fmt: skip
            rows.append(result)
            with out.open("a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(result.to_dict()) + "\n")
            print(
                f"[{name}] {row.id} "
                + " ".join(f"{m}={_cell(getattr(result, m))}" for m in METRICS)
            )
        summaries[name], counts[name] = summarise(rows), len(rows)
    path = settings.paths.resolve(settings.paths.results_dir) / "ragas.md"
    report = render(summaries, counts, settings.llm.ragas_model)
    path.write_text(report, encoding="utf-8", newline="\n")
    print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

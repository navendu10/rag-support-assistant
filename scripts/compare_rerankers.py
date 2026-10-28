"""Compare cross-encoder rerankers on the golden set (retrieval only, no LLM calls).

Usage:
    python scripts/compare_rerankers.py
    python scripts/compare_rerankers.py --stage v2 --fetch-k 20 --models BAAI/bge-reranker-base

Each question's fetch_k candidates come from the search stage (v2 hybrid search) once;
every model then reranks the same candidates. Prints recall@5, hit@1, MRR and ctx (see
docs/evaluation.md) for all questions and the ranking tag, plus mean/p95 rerank latency
on this machine after a warm-up call. Saves results/v3/reranker_comparison.md.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date

from rag_assistant.config import get_settings, load_stage
from rag_assistant.embeddings import Embedder
from rag_assistant.eval.rerank_eval import latency_stats, rank_table
from rag_assistant.eval.sweep import metrics_cell, score_group
from rag_assistant.ingestion.store import connect
from rag_assistant.retrieval.factory import make_retriever
from rag_assistant.retrieval.rerank import Reranker
from rag_assistant.schemas import load_golden

DEFAULT_MODELS = "Xenova/ms-marco-MiniLM-L-6-v2,BAAI/bge-reranker-base"
GROUPS = ("all", "ranking")


def parse_args() -> argparse.Namespace:
    """Parse command-line options."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stage", default="v2", help="stage whose search makes the candidates")
    parser.add_argument("--fetch-k", type=int, default=20, help="candidates per question")
    parser.add_argument("--models", default=DEFAULT_MODELS, help="comma-separated models")
    parser.add_argument("--out-stage", default="v3", help="results/<out-stage>/ for the file")
    args = parser.parse_args()
    args.models = [m.strip() for m in args.models.split(",") if m.strip()]
    return args


def main() -> int:
    """Rerank the same candidates with each model and save the comparison."""
    args = parse_args()
    stage, settings = load_stage(args.stage), get_settings()
    items = load_golden(settings.paths.resolve(settings.paths.golden_file))
    scored = [i for i in items if i.expected_doc_ids]
    groups = {"all": scored, "ranking": [i for i in scored if i.failure_tag == "ranking"]}
    manifest = settings.paths.resolve(settings.paths.generated_dir) / "manifest.json"

    with connect(settings.weaviate) as client:
        if not client.collections.exists(stage.collection_name):
            print(f"Run first: python scripts/ingest.py --stage {stage.index_owner}")
            return 1
        retriever = make_retriever(
            stage,
            client.collections.get(stage.collection_name),
            Embedder(settings.embedding),
            manifest,
        )
        candidates = {i.id: retriever.search(i.question, args.fetch_k) for i in items}

    runs = {f"{stage.name} search order": {k: [c.doc_id for c in v] for k, v in candidates.items()}}
    latencies: dict[str, dict[str, float]] = {}
    for model in args.models:
        print(f"Reranking with {model} ...", flush=True)
        reranker = Reranker(model)
        reranker.warm_up()
        ranked, times = {}, []
        for item in items:
            start = time.perf_counter()
            ranked[item.id] = [
                c.doc_id for c in reranker.rerank(item.question, candidates[item.id])
            ]
            times.append((time.perf_counter() - start) * 1000)
        runs[model] = ranked
        latencies[model] = latency_stats(times)

    header = "| Setting | " + " | ".join(f"{g} (n={len(groups[g])})" for g in GROUPS)
    lines = [header + " | rerank ms mean / p95 |", "|---|" + "---|" * (len(GROUPS) + 1)]
    for label, run in runs.items():
        cells = [metrics_cell(score_group(groups[g], run, stage.top_k)) for g in GROUPS]
        lat = latencies.get(label)
        timing = f"{lat['mean']:.0f} / {lat['p95']:.0f}" if lat else "-"
        lines.append(f"| {label} | " + " | ".join(cells) + f" | {timing} |")

    sections = [
        "# Reranker comparison",
        "\n".join(
            [
                f"- Date: {date.today().isoformat()}; retrieval only, no LLM calls",
                f"- Candidates: top {args.fetch_k} from stage `{stage.name}` "
                f"({stage.search} search, collection {stage.collection_name}), the same for "
                "every model",
                "- Each cell: recall@5 · hit@1 · MRR · ctx (expected-doc chunks among the "
                f"top {stage.top_k}); see docs/evaluation.md",
                f"- Latency: {len(items)} questions on this machine (CPU), after one warm-up "
                "call per model",
            ]
        ),
        "## Results",
        "\n".join(lines),
        "## Rank of the first expected chunk (questions where the runs differ)",
        rank_table(scored, runs),
    ]
    text = "\n\n".join(sections) + "\n"
    sys.stdout.reconfigure(encoding="utf-8")
    print(text)
    out = settings.paths.resolve(settings.paths.results_dir) / args.out_stage
    out.mkdir(parents=True, exist_ok=True)
    (out / "reranker_comparison.md").write_text(text, encoding="utf-8", newline="\n")
    print(f"Saved {out / 'reranker_comparison.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Sweep hybrid-search alpha and fusion over the golden set (retrieval only, no LLM calls).

Usage:
    python scripts/sweep_alpha.py --stage v2
    python scripts/sweep_alpha.py --stage v2 --alphas 0,0.25,0.5,0.75,1 --fusion ranked

Prints recall@5, hit@1 and MRR for all questions, product_code questions, and
questions with and without a detected code, at each (fusion, alpha). Also shows
every dynamic (code_alpha, default_alpha) pair, the stage's own setting and plain
vector search for reference. Saves everything to results/<stage>/alpha_sweep.md.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date

from weaviate.classes.query import MetadataQuery

from rag_assistant.config import get_settings, load_stage
from rag_assistant.embeddings import Embedder
from rag_assistant.eval.sweep import dynamic_matrix, dynamic_run, question_groups, sweep_table
from rag_assistant.ingestion.store import connect
from rag_assistant.retrieval.codes import CodeDetector
from rag_assistant.retrieval.hybrid import FUSION, hybrid_search, plan_query
from rag_assistant.schemas import load_golden

TOP_K = 5


def parse_args() -> argparse.Namespace:
    """Parse command-line options."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stage", required=True, help="a stage with search: hybrid, e.g. v2")
    parser.add_argument("--alphas", default="0,0.25,0.5,0.75,1", help="comma-separated alphas")
    parser.add_argument(
        "--fusion",
        default=",".join(FUSION),
        help=f"comma-separated fusion types from {', '.join(FUSION)}",
    )
    args = parser.parse_args()
    args.alphas = sorted({float(a) for a in args.alphas.split(",")})
    args.fusion = [f.strip() for f in args.fusion.split(",")]
    if not all(0.0 <= a <= 1.0 for a in args.alphas):
        parser.error("alphas must be between 0 and 1")
    if unknown := set(args.fusion) - set(FUSION):
        parser.error(f"unknown fusion type(s): {', '.join(sorted(unknown))}")
    return args


def main() -> int:
    """Run the sweep, print the tables and save them."""
    args = parse_args()
    stage, settings = load_stage(args.stage), get_settings()
    if stage.hybrid is None:
        print(f"Stage {stage.name} has search: vector; the sweep needs a hybrid stage.")
        return 1
    cfg = stage.hybrid
    items = load_golden(settings.paths.resolve(settings.paths.golden_file))
    detector = CodeDetector.from_manifest(
        settings.paths.resolve(settings.paths.generated_dir) / "manifest.json"
    )
    vectors = Embedder(settings.embedding).embed([i.question for i in items])
    keywords = {i.id: detector.canonicalise(i.question) for i in items}
    groups = question_groups(items, detector)

    with connect(settings.weaviate) as client:
        if not client.collections.exists(stage.collection_name):
            print(f"Run first: python scripts/ingest.py --stage {stage.name}")
            return 1
        collection = client.collections.get(stage.collection_name)

        def run(alpha: float, fusion: str) -> dict[str, list[str]]:
            """Top-5 doc_ids per question for one setting."""
            return {
                i.id: [
                    c.doc_id
                    for c in hybrid_search(
                        collection, keywords[i.id], v, alpha, fusion, cfg.query_properties, TOP_K
                    )
                ]
                for i, v in zip(items, vectors, strict=True)
            }

        runs = {(f, a): run(a, f) for f in args.fusion for a in args.alphas}
        vector_only = {
            i.id: [
                str(o.properties["doc_id"])
                for o in collection.query.near_vector(
                    near_vector=v, limit=TOP_K, return_metadata=MetadataQuery(distance=True)
                ).objects
            ]
            for i, v in zip(items, vectors, strict=True)
        }
        configured = {
            i.id: [
                c.doc_id
                for c in hybrid_search(
                    collection,
                    keywords[i.id],
                    v,
                    plan_query(i.question, cfg, detector).alpha,
                    cfg.fusion,
                    cfg.query_properties,
                    TOP_K,
                )
            ]
            for i, v in zip(items, vectors, strict=True)
        }

    if cfg.alpha_strategy == "fixed":
        setting = f"fixed alpha={cfg.alpha:g}"
    else:
        setting = f"dynamic code_alpha={cfg.code_alpha:g}, default_alpha={cfg.default_alpha:g}"
    rows = [("vector only (near_vector, as v1)", vector_only)]
    rows += [(f"{f}, alpha={a:g}", runs[(f, a)]) for f in args.fusion for a in args.alphas]
    rows.append((f"**{stage.name} as configured: {cfg.fusion}, {setting}**", configured))
    coded = [i.id for i in groups["code detected"]]

    sections = [
        f"# Alpha sweep: stage `{stage.name}`",
        "\n".join(
            [
                f"- Date: {date.today().isoformat()}; retrieval only, no LLM calls",
                f"- Query properties: `{', '.join(cfg.query_properties)}`; top {TOP_K} chunks",
                "- alpha = 1 is pure vector search, alpha = 0 is pure keyword (BM25) search",
                "- Each cell: recall@5 · hit@1 · MRR · ctx (see docs/evaluation.md). ctx counts"
                f" the top {stage.top_k} context chunks that come from an expected doc",
                f"- Questions with a detected code: {', '.join(coded)}",
            ]
        ),
        "## Fixed alpha",
        sweep_table(rows, groups, stage.top_k),
    ]
    for fusion in args.fusion:
        sections += [
            f"## Dynamic alpha, {fusion} fusion (all questions)",
            dynamic_matrix(
                {a: runs[(fusion, a)] for a in args.alphas}, groups["all"], detector, stage.top_k
            ),
        ]
    # Make sure the dynamic assembly agrees with a real dynamic query when one is configured.
    if cfg.alpha_strategy == "dynamic" and cfg.fusion in args.fusion:
        code_run, default_run = (
            runs.get((cfg.fusion, cfg.code_alpha)),
            runs.get((cfg.fusion, cfg.default_alpha)),
        )
        if code_run and default_run:
            assembled = dynamic_run(code_run, default_run, items, detector)
            if assembled != configured:
                print("warning: assembled dynamic run differs from the configured query")
    text = "\n\n".join(sections) + "\n"
    sys.stdout.reconfigure(encoding="utf-8")
    print(text)
    out = settings.paths.resolve(settings.paths.results_dir) / stage.name / "alpha_sweep.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8", newline="\n")
    print(f"Saved {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

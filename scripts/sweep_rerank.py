"""Sweep reranker input, fusion, fetch_k and model (retrieval only, no LLM calls).

Usage:
    python scripts/sweep_rerank.py

Phase 1: ms-marco-MiniLM-L-6-v2 with every reranker input variant (see
rag_assistant.retrieval.rerank_input) x fusion (replace / rrf / blend) x fetch_k
(10 / 20). Phase 2: the best phase-1 setting on bge-reranker-base and on
jina-reranker-v1-turbo-en, at its shipped 512-token limit and raised to 1024.

Candidates are v2's hybrid-search results (top 20; the top 10 is a prefix of them).
A chunk's rerank score doesn't depend on the other candidates, so each (model, input)
pair scores every question once and every fusion and fetch_k reuses those scores.
Metrics are computed against v2's own top 5 (the baseline). Saves
results/v3/rerank_sweep.md.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import get_args

from tokenizers import Tokenizer

from rag_assistant.config import RerankFusion, get_settings, load_stage
from rag_assistant.embeddings import Embedder
from rag_assistant.eval.rerank_eval import (
    SweepResult,
    contaminated,
    latency_stats,
    pick_setting,
    rank_cell,
    regressions,
)
from rag_assistant.eval.sweep import metrics_cell, score_group
from rag_assistant.ingestion.store import connect
from rag_assistant.retrieval.codes import CodeDetector
from rag_assistant.retrieval.factory import make_retriever
from rag_assistant.retrieval.rerank import Reranker, fused_order
from rag_assistant.retrieval.rerank_input import RerankInput, reranker_inputs
from rag_assistant.retrieval.vector import RetrievedChunk
from rag_assistant.schemas import GoldenItem, load_golden, load_manifest

MINILM = "Xenova/ms-marco-MiniLM-L-6-v2"
OTHER_MODELS = (
    ("BAAI/bge-reranker-base", None),
    ("jinaai/jina-reranker-v1-turbo-en", None),
    ("jinaai/jina-reranker-v1-turbo-en", 1024),
)
FUSIONS: tuple[RerankFusion, ...] = get_args(RerankFusion)
INPUTS: tuple[RerankInput, ...] = get_args(RerankInput)
FETCH_KS = (10, 20)
WATCH = ("g012", "g017", "g020")
TOP_K = 4


@dataclass(frozen=True)
class Scored:
    """One (model, input) pass: a logit per candidate, plus cost and length stats."""

    logits: dict[str, list[float]]
    latency: dict[str, float]
    pairs: int
    truncated: int
    max_tokens: int


def score_pass(
    reranker: Reranker,
    tokenizer: Tokenizer,
    limit: int,
    items: Sequence[GoldenItem],
    candidates: dict[str, list[RetrievedChunk]],
    variant: RerankInput,
) -> Scored:
    """Score every candidate of every question; a chunk's logit is its best input's."""
    logits, times = {}, []
    pairs = truncated = max_tokens = 0
    for item in items:
        pieces = [reranker_inputs(c.text, variant) for c in candidates[item.id]]
        flat = [p for group in pieces for p in group]
        start = time.perf_counter()
        scores = reranker.score(item.question, flat)
        times.append((time.perf_counter() - start) * 1000)
        lengths = [len(tokenizer.encode(item.question, p).ids) for p in flat]
        pairs += len(flat)
        truncated += sum(n > limit for n in lengths)
        max_tokens = max(max_tokens, *lengths)
        out, pos = [], 0
        for group in pieces:
            out.append(max(scores[pos : pos + len(group)]))
            pos += len(group)
        logits[item.id] = out
    return Scored(logits, {"mean": latency_stats(times)["mean"]}, pairs, truncated, max_tokens)


def load_reranker(model: str, max_length: int | None) -> tuple[Reranker, Tokenizer, int]:
    """A warmed-up reranker, an untruncated copy of its tokenizer, and its input limit."""
    reranker = Reranker(model)
    reranker.warm_up()
    tokenizer = reranker._model.model.tokenizer  # noqa: SLF001 - FastEmbed exposes no setter
    if max_length is not None:
        tokenizer.enable_truncation(max_length=max_length)
    limit = int(tokenizer.truncation["max_length"])
    copy = Tokenizer.from_str(tokenizer.to_str())
    copy.no_truncation()
    copy.no_padding()
    return reranker, copy, limit


def ordered_docs(
    candidates: list[RetrievedChunk],
    logits: list[float],
    fusion: RerankFusion,
    fetch_k: int,
) -> list[str]:
    """Doc ids of the first ``fetch_k`` candidates in fused order."""
    pool = candidates[:fetch_k]
    order = fused_order([c.score for c in pool], logits[:fetch_k], fusion)
    return [pool[i].doc_id for i in order]


def main() -> int:  # noqa: PLR0915 - a linear experiment script
    """Run both phases and save the tables."""
    settings, v2 = get_settings(), load_stage("v2")
    items = load_golden(settings.paths.resolve(settings.paths.golden_file))
    scored_items = [i for i in items if i.expected_doc_ids]
    tags = list(dict.fromkeys(i.failure_tag for i in scored_items))
    manifest = settings.paths.resolve(settings.paths.generated_dir) / "manifest.json"
    detector = CodeDetector.from_manifest(manifest)
    doc_codes = {d.doc_id: d.product_code for d in load_manifest(manifest) if d.product_code}
    codes = {i.id: detector.find(i.question) for i in items}

    with connect(settings.weaviate) as client:
        retriever = make_retriever(
            v2, client.collections.get(v2.collection_name), Embedder(settings.embedding), manifest
        )
        candidates = {i.id: retriever.search(i.question, max(FETCH_KS)) for i in items}
    baseline = {k: [c.doc_id for c in v] for k, v in candidates.items()}

    def evaluate(label: str, run: dict[str, list[str]], latency: float) -> tuple[SweepResult, str]:
        s = score_group(scored_items, run, TOP_K)
        reg = regressions(scored_items, baseline, run)
        dirty = sum(contaminated(codes[i.id], run[i.id][:TOP_K], doc_codes) for i in items)
        result = SweepResult(
            label,
            s["recall_at_k"]["count"],
            s["hit_at_1"]["count"],
            s["mrr"]["value"],
            len(reg.lost_recall),
            len(reg.lost_recall) + len(reg.lost_hit1),
            dirty,
            latency,
        )
        watch = " ".join(rank_cell(next(i for i in items if i.id == q), run[q]) for q in WATCH)
        row = (
            f"| {label} | {metrics_cell(s)} | {', '.join(reg.lost_recall) or '-'} | "
            f"{', '.join(reg.gained_recall) or '-'} | {', '.join(reg.lost_hit1) or '-'} | "
            f"{len(reg.gained_hit1)} | {dirty}/9 | {watch} | {latency:.0f} |"
        )
        return result, row

    header = (
        "| Setting | all (n=39): recall@5 · hit@1 · MRR · ctx | lost recall@5 vs v2 | "
        "gained recall@5 | lost hit@1 | hit@1 gains | coded contaminated | "
        "rank g012 g017 g020 | rerank ms mean |\n|---|---|---|---|---|---|---|---|---|"
    )
    base_result, base_row = evaluate("**v2 (hybrid, no rerank)**", baseline, 0.0)
    runs: dict[str, dict[str, list[str]]] = {base_result.label: baseline}

    print(f"Phase 1: {MINILM}", flush=True)
    reranker, tokenizer, limit = load_reranker(MINILM, None)
    passes = {v: score_pass(reranker, tokenizer, limit, items, candidates, v) for v in INPUTS}
    phase1, rows1 = [], [header, base_row]
    for variant in INPUTS:
        for fusion in FUSIONS:
            for fetch_k in FETCH_KS:
                label = f"{variant}, {fusion}, fetch_k {fetch_k}"
                run = {
                    i.id: ordered_docs(
                        candidates[i.id], passes[variant].logits[i.id], fusion, fetch_k
                    )
                    for i in items
                }
                runs[label] = run
                result, row = evaluate(label, run, passes[variant].latency["mean"])
                phase1.append(result)
                rows1.append(row)
    best = pick_setting(phase1, base_result.recall)
    fallback = best or min(phase1, key=lambda r: (r.lost_recall, -r.hit1, -r.mrr))
    variant, fusion, fetch_text = (p.strip() for p in fallback.label.split(","))
    fetch_k = int(fetch_text.split()[-1])

    length_rows = [
        "| Input | pairs scored | over the limit (truncated) | longest pair (tokens) |",
        "|---|---|---|---|",
    ] + [f"| {v} | {p.pairs} | {p.truncated} | {p.max_tokens} |" for v, p in passes.items()]

    rows2 = [header, base_row]
    for label in (fallback.label,):
        rows2.append(evaluate(f"{MINILM}: {label}", runs[label], fallback.latency_ms)[1])
    for model, max_length in OTHER_MODELS:
        print(f"Phase 2: {model} (max_length={max_length or 'default'})", flush=True)
        other, other_tok, other_limit = load_reranker(model, max_length)
        p = score_pass(other, other_tok, other_limit, items, candidates, variant)  # type: ignore[arg-type]
        run = {
            i.id: ordered_docs(candidates[i.id], p.logits[i.id], fusion, fetch_k)  # type: ignore[arg-type]
            for i in items
        }
        name = f"{model} (limit {other_limit}{', raised' if max_length else ''})"
        rows2.append(evaluate(f"{name}: {fallback.label}", run, p.latency["mean"])[1])
        length_rows.append(f"| {variant} on {name} | {p.pairs} | {p.truncated} | {p.max_tokens} |")

    tag_rows = ["| Setting | " + " | ".join(tags) + " |", "|---|" + "---|" * len(tags)]
    shown = [
        base_result.label,
        "chunk, replace, fetch_k 20",
        "chunk, replace, fetch_k 10",
        "chunk, rrf, fetch_k 10",
        fallback.label,
    ]
    for label in dict.fromkeys(shown):
        cells = []
        for tag in tags:
            group = [i for i in scored_items if i.failure_tag == tag]
            s = score_group(group, runs[label], TOP_K)
            cells.append(
                f"{s['recall_at_k']['count']}/{s['recall_at_k']['total']} · "
                f"{s['hit_at_1']['count']} · {s['mrr']['value']:.2f}"
            )
        tag_rows.append(f"| {label} | " + " | ".join(cells) + " |")

    verdict = (
        f"**Best setting that loses no question's recall@5 against v2 (and keeps "
        f"recall@5 >= {base_result.recall}/39): "
        f"`{best.label}`** (hit@1 {best.hit1}/39, MRR {best.mrr:.2f})."
        if best
        else "**No setting keeps every question's recall@5 against v2.** The closest "
        f"is `{fallback.label}` (recall {fallback.recall}/39, hit@1 {fallback.hit1}/39)."
    )
    sections = [
        "# Rerank sweep (retrieval only)",
        "\n".join(
            [
                f"- Date: {date.today().isoformat()}; no LLM calls; 44 golden questions, "
                "39 with an expected document",
                "- Candidates: v2 hybrid search, top 20 (fetch_k 10 = its first 10). "
                "The LLM would still receive the original chunk text",
                "- Inputs: chunk (as stored), body (no header), linear (table rows as "
                "sentences), groups (tables split into 4-row groups, best group scores), "
                "linear_groups",
                "- Fusion: replace (rerank only), rrf (1/(60+search rank) + 1/(60+rerank "
                "rank)), blend (0.5 sigmoid(logit) + 0.5 min-max(search score)). "
                "k=60, weight 0.5 and 4 rows per group are fixed, not tuned",
                "- Regressions and gains are per question against v2's own top 5. "
                "'coded contaminated': questions with a product code whose top 4 hold "
                "another product's chunk",
                "- Tuned on the same golden set used for scoring, so all of this is optimistic",
            ]
        ),
        verdict,
        "## Phase 1: ms-marco-MiniLM-L-6-v2",
        "\n".join(rows1),
        "## Phase 2: the best phase-1 setting on other models",
        "\n".join(rows2),
        "## Per tag (recall@5 · hit@1 · MRR)",
        "\n".join(tag_rows),
        "## Reranker input lengths",
        "\n".join(length_rows),
    ]
    text = "\n\n".join(sections) + "\n"
    sys.stdout.reconfigure(encoding="utf-8")
    print(text)
    out = settings.paths.resolve(settings.paths.results_dir) / "v3" / "rerank_sweep.md"
    out.write_text(text, encoding="utf-8", newline="\n")
    print(f"Saved {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

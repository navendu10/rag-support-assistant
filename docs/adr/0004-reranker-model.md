# ADR 0004: MiniLM-L-6 cross-encoder, blended with the search score

- **Status:** Accepted (revised the same day after the rerank sweep)
- **Date:** 2026-10-28

## Context

Failure #3 (right answer ranked too low) needs a second-stage ranker. Hybrid search (v2)
is a bi-encoder: the question and each chunk are embedded separately, so it scores
similarity. A cross-encoder reads the question and the chunk together and scores
relevance. It is too slow for all 162 chunks, so v3 reranks the top 20 search candidates
(`fetch_k: 20`).

Constraints:

- **Free, open source and local**, like the rest of the stack (no Cohere or Voyage API).
- **FastEmbed** is already a dependency, and its `TextCrossEncoder` runs ONNX models on
  CPU with no PyTorch, so no new package is needed.
- **CPU latency.** A support chat can't wait many seconds before the LLM even starts.

Two decisions were needed: **which model**, and **how its score combines with the
search ranking**.

## Model: options measured

`python scripts/compare_rerankers.py` reranks the same 20 v2 candidates per question with
each model and makes no LLM calls. The figures below are for **rerank-only** ordering;
full output is in [`reranker_comparison.md`](../../results/v3/reranker_comparison.md).

| Model | Size | Licence | Max input (FastEmbed) | Hit@1 | MRR | Recall@5 | Rerank ms mean / p95 |
|---|---|---|---|---|---|---|---|
| none (v2 search order) | - | - | - | 22/39 | 0.68 | 34/39 | - |
| `Xenova/ms-marco-MiniLM-L-6-v2` | 80 MB | Apache-2.0 | 512 | 26/39 | 0.74 | 33/39 | 1,414 / 2,520 |
| `BAAI/bge-reranker-base` | 1.04 GB | MIT | 512 | 27/39 | 0.74 | 32/39 | 6,464 / 10,659 |
| `Xenova/ms-marco-MiniLM-L-12-v2` (extra check) | 120 MB | Apache-2.0 | 512 | 25/39 | 0.72 | 32/39 | 2,122 / 3,558 |

`jinaai/jina-reranker-v1-turbo-en` (150 MB, Apache-2.0) is advertised with an 8K context,
and its model really does support 8,192 positions, but FastEmbed reads `model_max_length:
512` from its tokenizer config. It was tested in the sweep at 512 and raised to 1024.
Results were identical, because no raw-chunk pair exceeds 485 of its tokens, and it was
weaker than MiniLM (hit@1 23, recall 34 with the chosen fusion).

## Fusion: options measured

Rerank-only ordering raised hit@1 but pushed three answers out of the top 5 that search
had found (g012, g017, g020). It also put the twin router manuals back into code
questions' context. `python scripts/sweep_rerank.py` (no LLM calls) compared rerank-only,
reciprocal rank fusion (k = 60) and a 0.5/0.5 blend, across five reranker input variants
and fetch_k 10 or 20, with MiniLM. It then tried the best setting on the other models.
The rule was that no question may lose recall@5 against v2, then the most hit@1 wins.
Full output is in [`rerank_sweep.md`](../../results/v3/rerank_sweep.md).

| Setting | recall@5 | hit@1 | MRR | Lost recall vs v2 | Coded contaminated |
|---|---|---|---|---|---|
| MiniLM, rerank only, 20 | 33 | 26 | 0.74 | g012, g017, g020 | 5/9 |
| MiniLM, rrf, 10 | 37 | 23 | 0.72 | – | 4/9 |
| **MiniLM, blend 0.5, 20** | **36** | **24** | **0.73** | **–** | **4/9** |
| bge-reranker-base, blend 0.5, 20 (linearised tables) | 34 | 23 | 0.70 | g001 | 4/9 |
| jina-turbo, blend 0.5, 20 (linearised tables) | 34 | 23 | 0.69 | – | 2/9 |

## Decision

Use **`Xenova/ms-marco-MiniLM-L-6-v2`** via FastEmbed's `TextCrossEncoder` with
**`rerank_fusion: blend`** and **`rerank_blend_weight: 0.5`**. The order is 0.5 ×
sigmoid(rerank logit) plus 0.5 × the min-max-normalised hybrid score, the reranker reads the
chunk text as stored, and `fetch_k` is 20.

- **Model:** MiniLM ties bge-reranker-base on MRR and is about 4.6× faster and 13× smaller.
  With the blend, bge does worse, because it loses g001.
- **Blend:** it is the setting with the most hit@1 among those that lose no question
  against v2. `linear, blend, 20` tied it, and was not chosen because linearising adds 23%
  rerank time and truncates 124 inputs for no headline gain.
- **End to end:** correctness 27 → 29 of 44 against v2, with no answer getting worse.

## Consequences

- **Only half of rerank-only's hit@1 gain is kept** (24 against 26). The blend weight 0.5
  was not tuned; a higher weight would trade recall back for hit@1.
- **Code contamination is reduced, not removed.** 4 of 9 coded questions still get the
  twin router's chunks (0/9 in v2). Filtering candidates by the detected `product_code`
  before reranking is the follow-up.
- **The reranker scores internal playbooks highly for leak questions**, correctly, so
  access control (Session 7) must filter before search and reranking.
- **Tables score near zero** with an MS MARCO model. Table linearisation and row groups
  are implemented (`retrieval/rerank_input.py`) but not used, because they didn't help
  under fusion.
- **Rerank latency is about 1.1 s mean and 2.0 s p95** for 20 chunks on this CPU, against
  21 ms for hybrid search. Options if it matters: a smaller `fetch_k` (10 had similar
  quality with RRF) or a GPU.
- **Scores are raw logits** (about −11 to +10). Every chunk also stores `sigmoid(logit)` as a
  0-1 `rerank_score`, whatever the fusion, for Session 8's `min_score`.
- **Tuning caveat.** The model, the fusion and fetch_k were chosen on the same golden set
  that scores v3, so v3's numbers are optimistic (see
  [docs/evaluation.md](../evaluation.md)).
- Changing either choice is a stage-file edit (`rerank_model`, `rerank_fusion`,
  `rerank_blend_weight`) plus a re-run of the sweep scripts.

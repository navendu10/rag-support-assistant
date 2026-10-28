# Rerank sweep (retrieval only)

- Date: 2026-10-28; no LLM calls; 44 golden questions, 39 with an expected document
- Candidates: v2 hybrid search, top 20 (fetch_k 10 = its first 10). The LLM would still receive the original chunk text
- Inputs: chunk (as stored), body (no header), linear (table rows as sentences), groups (tables split into 4-row groups, best group scores), linear_groups
- Fusion: replace (rerank only), rrf (1/(60+search rank) + 1/(60+rerank rank)), blend (0.5 sigmoid(logit) + 0.5 min-max(search score)). k=60, weight 0.5 and 4 rows per group are fixed, not tuned
- Regressions and gains are per question against v2's own top 5. 'coded contaminated': questions with a product code whose top 4 hold another product's chunk
- Tuned on the same golden set used for scoring, so all of this is optimistic

**Best setting that loses no question's recall@5 against v2 (and keeps recall@5 >= 34/39): `linear, blend, fetch_k 20`** (hit@1 24/39, MRR 0.73).

## Phase 1: ms-marco-MiniLM-L-6-v2

| Setting | all (n=39): recall@5 · hit@1 · MRR · ctx | lost recall@5 vs v2 | gained recall@5 | lost hit@1 | hit@1 gains | coded contaminated | rank g012 g017 g020 | rerank ms mean |
|---|---|---|---|---|---|---|---|---|
| **v2 (hybrid, no rerank)** | 34/39 · 22/39 · 0.68 · 98/156 | - | - | - | 0 | 0/9 | 2 3 3 | 0 |
| chunk, replace, fetch_k 10 | 36/39 · 25/39 · 0.74 · 92/156 | g012 | g013, g015, g032 | g043 | 4 | 5/9 | 8 5 3 | 1029 |
| chunk, replace, fetch_k 20 | 33/39 · 26/39 · 0.74 · 87/156 | g012, g017, g020 | g013, g032 | g043 | 5 | 5/9 | 7 7 6 | 1029 |
| chunk, rrf, fetch_k 10 | 37/39 · 23/39 · 0.72 · 96/156 | - | g013, g015, g032 | - | 1 | 4/9 | 3 3 3 | 1029 |
| chunk, rrf, fetch_k 20 | 36/39 · 23/39 · 0.72 · 95/156 | - | g013, g015 | - | 1 | 4/9 | 3 3 3 | 1029 |
| chunk, blend, fetch_k 10 | 35/39 · 23/39 · 0.70 · 94/156 | - | g013 | - | 1 | 4/9 | 3 3 3 | 1029 |
| chunk, blend, fetch_k 20 | 36/39 · 24/39 · 0.73 · 95/156 | - | g013, g032 | - | 2 | 4/9 | 3 3 3 | 1029 |
| body, replace, fetch_k 10 | 33/39 · 22/39 · 0.67 · 81/156 | g003, g012 | g032 | g042 | 1 | 5/9 | 6 5 3 | 944 |
| body, replace, fetch_k 20 | 31/39 · 23/39 · 0.68 · 81/156 | g003, g012, g017, g020 | g032 | g042 | 2 | 5/9 | 6 7 6 | 944 |
| body, rrf, fetch_k 10 | 35/39 · 23/39 · 0.70 · 91/156 | - | g032 | - | 1 | 2/9 | 3 3 3 | 944 |
| body, rrf, fetch_k 20 | 35/39 · 23/39 · 0.69 · 87/156 | - | g032 | - | 1 | 3/9 | 4 3 3 | 944 |
| body, blend, fetch_k 10 | 34/39 · 23/39 · 0.69 · 90/156 | - | - | - | 1 | 5/9 | 3 3 3 | 944 |
| body, blend, fetch_k 20 | 34/39 · 23/39 · 0.70 · 90/156 | g003 | g032 | - | 1 | 5/9 | 3 3 3 | 944 |
| linear, replace, fetch_k 10 | 35/39 · 23/39 · 0.71 · 89/156 | g001, g012 | g013, g015, g032 | g043 | 2 | 5/9 | 8 5 4 | 1262 |
| linear, replace, fetch_k 20 | 32/39 · 24/39 · 0.71 · 86/156 | g001, g012, g017, g020 | g013, g032 | g043 | 3 | 5/9 | 7 8 8 | 1262 |
| linear, rrf, fetch_k 10 | 37/39 · 23/39 · 0.72 · 96/156 | - | g013, g015, g032 | - | 1 | 4/9 | 3 3 3 | 1262 |
| linear, rrf, fetch_k 20 | 35/39 · 23/39 · 0.71 · 94/156 | - | g013 | - | 1 | 4/9 | 3 3 3 | 1262 |
| linear, blend, fetch_k 10 | 35/39 · 24/39 · 0.72 · 95/156 | - | g013 | - | 2 | 4/9 | 3 3 3 | 1262 |
| linear, blend, fetch_k 20 | 36/39 · 24/39 · 0.73 · 97/156 | - | g013, g032 | - | 2 | 4/9 | 3 3 3 | 1262 |
| groups, replace, fetch_k 10 | 33/39 · 25/39 · 0.72 · 87/156 | g001, g004, g012 | g013, g032 | g043 | 4 | 5/9 | 8 4 5 | 748 |
| groups, replace, fetch_k 20 | 32/39 · 26/39 · 0.73 · 86/156 | g001, g004, g012, g020 | g013, g032 | g043 | 5 | 5/9 | 7 4 10 | 748 |
| groups, rrf, fetch_k 10 | 36/39 · 23/39 · 0.71 · 94/156 | - | g013, g032 | - | 1 | 4/9 | 3 2 3 | 748 |
| groups, rrf, fetch_k 20 | 35/39 · 23/39 · 0.71 · 92/156 | - | g013 | - | 1 | 4/9 | 3 2 3 | 748 |
| groups, blend, fetch_k 10 | 35/39 · 23/39 · 0.70 · 94/156 | - | g013 | - | 1 | 4/9 | 3 3 3 | 748 |
| groups, blend, fetch_k 20 | 36/39 · 23/39 · 0.71 · 96/156 | - | g013, g032 | - | 1 | 4/9 | 3 3 3 | 748 |
| linear_groups, replace, fetch_k 10 | 34/39 · 25/39 · 0.73 · 88/156 | g004, g012 | g013, g032 | - | 3 | 5/9 | 8 5 4 | 1145 |
| linear_groups, replace, fetch_k 20 | 32/39 · 26/39 · 0.73 · 87/156 | g004, g012, g017, g020 | g013, g032 | - | 4 | 5/9 | 7 7 7 | 1145 |
| linear_groups, rrf, fetch_k 10 | 35/39 · 22/39 · 0.69 · 95/156 | g004 | g013, g032 | - | 0 | 3/9 | 3 3 3 | 1145 |
| linear_groups, rrf, fetch_k 20 | 34/39 · 22/39 · 0.69 · 93/156 | g004 | g013 | - | 0 | 4/9 | 3 3 3 | 1145 |
| linear_groups, blend, fetch_k 10 | 35/39 · 23/39 · 0.70 · 95/156 | - | g013 | - | 1 | 4/9 | 3 3 3 | 1145 |
| linear_groups, blend, fetch_k 20 | 36/39 · 23/39 · 0.72 · 96/156 | - | g013, g032 | - | 1 | 4/9 | 3 3 3 | 1145 |

## Phase 2: the best phase-1 setting on other models

| Setting | all (n=39): recall@5 · hit@1 · MRR · ctx | lost recall@5 vs v2 | gained recall@5 | lost hit@1 | hit@1 gains | coded contaminated | rank g012 g017 g020 | rerank ms mean |
|---|---|---|---|---|---|---|---|---|
| **v2 (hybrid, no rerank)** | 34/39 · 22/39 · 0.68 · 98/156 | - | - | - | 0 | 0/9 | 2 3 3 | 0 |
| Xenova/ms-marco-MiniLM-L-6-v2: linear, blend, fetch_k 20 | 36/39 · 24/39 · 0.73 · 97/156 | - | g013, g032 | - | 2 | 4/9 | 3 3 3 | 1262 |
| BAAI/bge-reranker-base (limit 512): linear, blend, fetch_k 20 | 34/39 · 23/39 · 0.70 · 93/156 | g001 | g032 | - | 1 | 4/9 | 3 3 3 | 6465 |
| jinaai/jina-reranker-v1-turbo-en (limit 512): linear, blend, fetch_k 20 | 34/39 · 23/39 · 0.69 · 95/156 | - | - | - | 1 | 2/9 | 3 4 3 | 1633 |
| jinaai/jina-reranker-v1-turbo-en (limit 1024, raised): linear, blend, fetch_k 20 | 34/39 · 23/39 · 0.69 · 95/156 | - | - | - | 1 | 2/9 | 3 4 3 | 2550 |

## Per tag (recall@5 · hit@1 · MRR)

| Setting | chunking | product_code | ranking | query_region | stale | leak | hallucination | injection | normal |
|---|---|---|---|---|---|---|---|---|---|
| **v2 (hybrid, no rerank)** | 3/4 · 0 · 0.21 | 8/8 · 7 · 0.94 | 2/3 · 0 · 0.28 | 2/4 · 1 · 0.38 | 2/2 · 0 · 0.33 | 3/3 · 1 · 0.56 | 1/1 · 0 · 0.50 | 2/2 · 2 · 1.00 | 11/12 · 11 · 0.92 |
| chunk, replace, fetch_k 20 | 3/4 · 1 · 0.40 | 8/8 · 6 · 0.88 | 2/3 · 1 · 0.50 | 2/4 · 2 · 0.50 | 1/2 · 1 · 0.50 | 2/3 · 1 · 0.44 | 1/1 · 0 · 0.50 | 2/2 · 2 · 1.00 | 12/12 · 12 · 1.00 |
| chunk, replace, fetch_k 10 | 3/4 · 1 · 0.40 | 8/8 · 6 · 0.88 | 2/3 · 1 · 0.50 | 3/4 · 2 · 0.56 | 2/2 · 1 · 0.60 | 3/3 · 1 · 0.56 | 1/1 · 0 · 0.50 | 2/2 · 2 · 1.00 | 12/12 · 11 · 0.94 |
| chunk, rrf, fetch_k 10 | 3/4 · 0 · 0.25 | 8/8 · 7 · 0.94 | 2/3 · 1 · 0.44 | 4/4 · 1 · 0.52 | 2/2 · 0 · 0.42 | 3/3 · 1 · 0.56 | 1/1 · 0 · 0.50 | 2/2 · 2 · 1.00 | 12/12 · 11 · 0.93 |
| linear, blend, fetch_k 20 | 3/4 · 1 · 0.40 | 8/8 · 7 · 0.94 | 2/3 · 1 · 0.44 | 3/4 · 1 · 0.40 | 2/2 · 0 · 0.42 | 3/3 · 1 · 0.56 | 1/1 · 0 · 0.50 | 2/2 · 2 · 1.00 | 12/12 · 11 · 0.96 |

## Reranker input lengths

| Input | pairs scored | over the limit (truncated) | longest pair (tokens) |
|---|---|---|---|
| chunk | 880 | 6 | 523 |
| body | 880 | 0 | 502 |
| linear | 880 | 124 | 806 |
| groups | 1363 | 0 | 221 |
| linear_groups | 1363 | 0 | 361 |
| linear on BAAI/bge-reranker-base (limit 512) | 880 | 124 | 944 |
| linear on jinaai/jina-reranker-v1-turbo-en (limit 512) | 880 | 124 | 771 |
| linear on jinaai/jina-reranker-v1-turbo-en (limit 1024, raised) | 880 | 0 | 771 |

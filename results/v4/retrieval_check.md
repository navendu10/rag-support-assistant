# Retrieval-only check: v3,v4a,v4

Written by `python scripts/check_rewrite_retrieval.py`. Recall@5 · hit@1 · MRR over the 42 golden questions with an expected document. *Stored* is the committed full run; *now* is the current code, retrieval only.

| Stage | Stored | Now |
|---|---|---|
| v3 | 38/42 · 25 · 0.71 | 38/42 · 25 · 0.71 |
| v4a | 39/42 · 28 · 0.75 | 39/42 · 28 · 0.75 |
| v4 | 40/42 · 29 · 0.81 | 40/42 · 29 · 0.81 |

## v3: first expected rank, stored -> now

- no change

## v4a: first expected rank, stored -> now

- no change

## v4: first expected rank, stored -> now

- no change

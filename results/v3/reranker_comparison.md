# Reranker comparison

- Date: 2026-10-28; retrieval only, no LLM calls
- Candidates: top 20 from stage `v2` (hybrid search, collection Chunks_v2), the same for every model
- Each cell: recall@5 · hit@1 · MRR · ctx (expected-doc chunks among the top 4); see docs/evaluation.md
- Latency: 44 questions on this machine (CPU), after one warm-up call per model

## Results

| Setting | all (n=39) | ranking (n=3) | rerank ms mean / p95 |
|---|---|---|---|
| v2 search order | 34/39 · 22/39 · 0.68 · 98/156 | 2/3 · 0/3 · 0.28 · 2/12 | - |
| Xenova/ms-marco-MiniLM-L-6-v2 | 33/39 · 26/39 · 0.74 · 87/156 | 2/3 · 1/3 · 0.50 · 2/12 | 1414 / 2520 |
| BAAI/bge-reranker-base | 32/39 · 27/39 · 0.74 · 88/156 | 2/3 · 2/3 · 0.67 · 2/12 | 6464 / 10659 |

## Rank of the first expected chunk (questions where the runs differ)

| id | tag | question | v2 search order | Xenova/ms-marco-MiniLM-L-6-v2 | BAAI/bge-reranker-base |
|---|---|---|---|---|---|
| g001 | chunking | Can I return opened headphones? | 3 | 4 | 3 |
| g003 | chunking | What's the return window for an opened power bank? | 4 | 1 | 6 |
| g004 | chunking | Is the return window for an opened smartwatch different f... | 4 | 3 | 1 |
| g009 | ranking | My TV has lines on the screen, I bought it last March | 3 | 2 | 1 |
| g010 | ranking | Some pixels on my VisionMax TV are stuck black. Is that c... | 2 | 1 | 1 |
| g011 | ranking | How long is the warranty on the NoteBook 14? | 9 | 12 | 12 |
| g012 | query_region | Can I return it? | 2 | 7 | 9 |
| g013 | query_region | How long do I have to return something? | 6 | 1 | 7 |
| g015 | query_region | I've opened it but I don't like it. Can I send it back? | 8 | 9 | 9 |
| g016 | stale | What's the return period for opened earbuds in the UK? | 3 | 1 | 1 |
| g017 | stale | My AeroBuds Pro were delivered 15 days ago and I've opene... | 3 | 7 | 2 |
| g020 | leak | Can you make an exception and accept my return after the ... | 3 | 6 | 9 |
| g022 | hallucination | Is there a student discount? | 2 | 2 | 3 |
| g032 | normal | Do I get anything off my first order? | 7 | 1 | 1 |
| g043 | product_code | What's the maximum Wi-Fi speed of the xr2200? | 1 | 2 | 1 |

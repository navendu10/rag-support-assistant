# RAG failure catalogue

Each failure is reproduced on purpose, measured on a golden set, fixed and re-measured.
Every entry gets its own page (`NN-name.md`) with: symptom, how to reproduce, root cause,
fix, and before/after numbers.

**Baseline:** [00-baseline.md](00-baseline.md) has v0's numbers, one real example per
failure, and an honest account of what did and did not reproduce.

**Fixes so far:** [01-chunking.md](01-chunking.md) (stage v1) and
[02-hybrid-search.md](02-hybrid-search.md) (stage v2) and
[03-reranking.md](03-reranking.md) (stage v3).

| # | Failure | Example symptom | Planned fix | Page |
|---|---------|-----------------|-------------|------|
| 1 | Bad chunking | Answer misses the half of a policy that landed in another chunk | Structure-aware chunking with overlap | [01-chunking](01-chunking.md) |
| 2 | Product-code confusion | An "XR-2200" question gets the XR-2000's reset steps in its context | Hybrid (BM25 + vector) search, keyword-heavy only when a code is detected | [02-hybrid-search](02-hybrid-search.md) |
| 3 | Poor ranking | Right chunk retrieved but ranked 8th | Cross-encoder reranking of the top 20 search candidates | [03-reranking](03-reranking.md) |
| 4 | Vague queries and wrong region | "How long is the warranty?" answered with the US policy for a UK customer | Query rewriting, clarification, region filters | _pending_ |
| 5 | Internal-document leaks | Staff-only margin notes quoted to a customer | Access-level metadata and filtering | _pending_ |
| 6 | Hallucination | Confident answer with no supporting source | Grounded prompts, citations, abstain when unsure | _pending_ |
| 7 | Prompt injection | A document or message says "ignore previous instructions" | Context sanitising, instruction hierarchy, output checks | _pending_ |

**Stale documents** (an outdated UK returns policy beside the current one) are tracked
under their own golden tag, `stale`. They are fixed alongside failure 4 by using the
`effective_date` metadata.

How the knowledge base triggers each failure, and which golden items measure it, is
described in [docs/data.md](../data.md).

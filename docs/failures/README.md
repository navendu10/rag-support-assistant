# RAG failure catalogue

Each failure is reproduced on purpose, measured on a golden set, fixed and re-measured.
Every entry gets its own page (`NN-name.md`) with: symptom, how to reproduce, root cause,
fix, and before/after numbers.

| # | Failure | Example symptom | Planned fix | Page |
|---|---------|-----------------|-------------|------|
| 1 | Bad chunking | Answer misses the half of a policy that landed in another chunk | Structure-aware chunking with overlap | _pending_ |
| 2 | Product-code confusion | "XB-200" retrieves "XB-2000" docs | Hybrid (BM25 + vector) search, metadata filters | _pending_ |
| 3 | Poor ranking | Right chunk retrieved but ranked 8th | Cross-encoder reranking | _pending_ |
| 4 | Vague queries and wrong region | "How long is the warranty?" answered with the US policy for a UK customer | Query rewriting, clarification, region filters | _pending_ |
| 5 | Internal-document leaks | Staff-only margin notes quoted to a customer | Access-level metadata and filtering | _pending_ |
| 6 | Hallucination | Confident answer with no supporting source | Grounded prompts, citations, abstain when unsure | _pending_ |
| 7 | Prompt injection | A document or message says "ignore previous instructions" | Context sanitising, instruction hierarchy, output checks | _pending_ |

**Stale documents** (an outdated UK returns policy beside the current one) are tracked
under their own golden tag, `stale`. They are fixed alongside failure 4 by using the
`effective_date` metadata.

How the knowledge base triggers each failure, and which golden items measure it, is
described in [docs/data.md](../data.md).

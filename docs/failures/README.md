# RAG failure catalogue

Each failure is reproduced on purpose, measured on a golden set, fixed and re-measured.
Every entry gets its own page (`NN-name.md`) with: symptom, how to reproduce, root cause,
fix, and before/after numbers.

**Baseline:** [00-baseline.md](00-baseline.md) has v0's numbers, one real example per
failure, and an honest account of what did and did not reproduce.

**Fixes so far:** [01-chunking.md](01-chunking.md) (stage v1) and
[02-hybrid-search.md](02-hybrid-search.md) (stage v2),
[03-reranking.md](03-reranking.md) (stage v3),
[04-query-and-region.md](04-query-and-region.md) (stages v4a and v4) and
[05-access-control.md](05-access-control.md) (stage v5) and
[06-grounding-and-handoff.md](06-grounding-and-handoff.md) (stages v6a and v6) and
[07-prompt-injection.md](07-prompt-injection.md) (stage v7). All seven are fixed.

| # | Failure | Example symptom | Planned fix | Page |
|---|---------|-----------------|-------------|------|
| 1 | Bad chunking | Answer misses the half of a policy that landed in another chunk | Structure-aware chunking with overlap | [01-chunking](01-chunking.md) |
| 2 | Product-code confusion | An "XR-2200" question gets the XR-2000's reset steps in its context | Hybrid (BM25 + vector) search, keyword-heavy only when a code is detected | [02-hybrid-search](02-hybrid-search.md) |
| 3 | Poor ranking | Right chunk retrieved but ranked 8th | Cross-encoder reranking of the top 20 search candidates | [03-reranking](03-reranking.md) |
| 4 | Vague queries and wrong region | "How long is the warranty?" answered with the US policy for a UK customer | Query rewriting with chat history (small model, catalogue), region filter from the user profile | [04-query-and-region](04-query-and-region.md) |
| 5 | Internal-document leaks | Staff-only margin notes quoted to a customer | Per-audience collections and Weaviate RBAC: the customer key can't read internal documents | [05-access-control](05-access-control.md) |
| 6 | Hallucination | "We don't price-match" when no document mentions price matching | Role-aware grounded prompts with numbered sources and checked citations; a LangGraph corrective flow that retries weak retrieval and hands unanswered questions to a person | [06-grounding-and-handoff](06-grounding-and-handoff.md) |
| 7 | Prompt injection | A hidden listing line makes the assistant promise a "lifetime warranty" | Four layers: sanitised third-party ingestion with trust levels, tagged "sources are data" prompts, an output guard on figures and promises, no secrets in the data; measured by an 18-attack red team | [07-prompt-injection](07-prompt-injection.md) |

**Stale documents** (an outdated UK returns policy beside the current one) are tracked
under their own golden tag, `stale`. The region filter can't fix them (the outdated
policy has the same region), so they wait for `effective_date` filtering in Session 14.

How the knowledge base triggers each failure, and which golden items measure it, is
described in [docs/data.md](../data.md).

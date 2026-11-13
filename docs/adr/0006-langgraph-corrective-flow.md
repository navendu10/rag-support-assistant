# ADR 0006: A LangGraph corrective flow, with handoff decided by the generation check

- **Status:** Accepted
- **Date:** 2026-11-12
- **Versions:** `langgraph` 1.2.15 (already installed as a LangChain 1.x dependency, now
  pinned in `requirements.txt` because it is imported directly), `langchain-core` 1.6.11.

## Context

Failure #6: when the retrieved context didn't cover a question, the naive prompt (v0-v5)
let the model fill the gap. v5 still told customers "we don't price-match" (g023),
described a process for requesting a goodwill credit (g018), refused an exception with a
policy no document states (g020), and described Linux support for a laptop whose manual
never mentions Linux (g024). Six of the seven `expect_handoff` questions were answered.

Two separate problems:

1. **Generation:** nothing told the model to stay inside the sources, cite them, or say
   when they don't answer.
2. **Control flow:** a one-pass pipeline has no point at which to notice weak retrieval,
   try again, or route to a human.

Session 8 measures the two separately: v6a adds grounding only, v6 adds the flow.

## Decision

1. **Grounded generation (v6a), `generation/grounded.py`.** Role-aware prompts (customer
   or agent, from the profile) with numbered sources; structured output
   (`answer`, `citations`, `found_in_sources`); code checks that every cited number
   exists; invalid output is retried once, then treated as "not found".
2. **A LangGraph state graph (v6), `graph/corrective.py`:**
   `rewrite -> retrieve -> grade -> (retry -> grade) -> generate -> check -> answer | handoff`.
   Each node is a small function over a typed state; the service calls come from a
   `FlowSteps` protocol that the pipeline implements and unit tests fake.
3. **One retry per question, two triggers, one budget.** The retry runs if no reranked
   candidate scores at least `min_score` (0.1, sigmoid rerank score), or if the check
   fails (not found or invalid) and the retry is unused. It is one wider search: the
   original message plus the resolved products' catalogue names and codes (from code),
   30 candidates instead of 20, and 8 context chunks instead of 4. Reranking and
   regeneration use the standalone question. A failed check after the retry hands off,
   so the graph can't loop. The retrieval grade is never a reason to hand off. (The first
   version had only the low-score trigger: the standalone question, 40 candidates, a
   4-chunk context.)
4. **Handoff is decided only by the generation check:** `found_in_sources` false, or
   invalid output or citations. Customers get a polite message and a ticket
   (`data/tickets/tickets.jsonl`, git-ignored); agents get "not found in the knowledge
   base" with the closest sources and no ticket. The model's own not-found answer is shown
   above the message only when it is valid and cites sources.
5. **The retry can't widen access.** It goes through the same `retrieve_ranked` call with
   the same user and region filter, so it uses the same Weaviate connection, key and
   collections. Unit tests prove it for customers and agents.

## Why LangGraph

- It is already in the dependency tree (LangChain 1.x depends on it), so pinning it adds
  no new package.
- The flow is a graph with a loop (grade -> retry -> grade) and a branch (answer or
  handoff). As an explicit graph, each node can be tested alone and the route is easy to
  record (`path` in every results row) and to draw in the docs.
- The agent of Sessions 10-11 is a LangGraph graph too, with tools and human approval.
  This flow is the first, small graph in the codebase and sets the pattern: typed state,
  small nodes, services behind a protocol.

## Alternatives considered

- **Plain Python control flow in `Pipeline.answer`.** Fewer moving parts for six steps,
  but the retry loop and the handoff branch would be buried in one long method, and the
  later agent would need a second, different style anyway.
- **Handing off on a low retrieval score.** Cheaper (no generation call), but in v3 seven
  of 39 answerable questions had their best expected chunk below 0.01. Many of them were
  still answered correctly from other chunks. A score threshold would turn them into
  false handoffs. The generation check sees the actual sources and the question.
- **Filtering context below `min_score`.** Removing weak chunks before generation would
  hide sources from the model, which raises false handoffs too. It may come back with the
  held-out set (Session 13).
- **A separate LLM "grader" node (classic CRAG).** One more small-model call per
  question on Groq's free tier. The cross-encoder score is already computed and free.

## Consequences

- v6 costs one wider search and rerank (about 2.3 s), plus a regeneration (about 0.85 s)
  after a not-found answer, for the 12 of 50 questions that retry, and nothing more for
  the rest: when no retry happens, its generation prompt is identical to v6a's and comes
  from the cache.
- Every results row records the route: grade, best score, retry used and kept, handoff
  reason, and whether a ticket was created.
- `min_score` was tuned on the same golden set that scores the stages, like every other
  threshold so far (see `docs/evaluation.md`).
- No prompt-injection defences were added, so Session 9 can measure them on their own.

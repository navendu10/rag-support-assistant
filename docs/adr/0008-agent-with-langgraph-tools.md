# ADR 0008: A LangGraph tool-calling agent that reuses v7 as its knowledge tool

- **Status:** Accepted
- **Date:** 2026-11-15
- **Versions:** `langgraph` 1.2.15 and `langchain-groq` 1.1.3 (no new dependency). The
  model is Groq `openai/gpt-oss-120b`, the existing large model; its tool calling was
  confirmed with a live `bind_tools` call before building on it.

## Context

v0-v7 answer from documents only. A support assistant also needs **the customer's own
data**: is my TV still under warranty, can I still return these earbuds, where is my
order? Those answers depend on dates and order records, not on policy text alone. A
language model is bad at date arithmetic and must never see another customer's orders.

## Decision

1. **A tool-calling agent (v8)**, a small custom LangGraph graph:
   `model → tools → model … → guard → END`.
   - The model is Groq `gpt-oss-120b` with the tools bound.
   - The system prompt is role-aware, and the role comes from the profile.
2. **Six tools**, built per session by `make_tools(session)`, each with a Pydantic input
   model:
   - `search_knowledge_base`: the **v7 pipeline** for the same user
   - `list_my_orders`, `get_order`, `check_warranty`, `check_return_eligibility`:
     read-only, with the business rules from `configs/business_rules.yaml` and a fixed
     reference date
   - `draft_ticket`: a draft only; submitting is Session 11
3. **The user is injected, never an argument.**
   - No tool takes a user or customer id.
   - A customer sees only their own orders; staff can look up any order by id but have
     none of their own.
   - Another customer's order and a missing one return the same "not found".
   - The order database is opened read-only.
4. **Dates and amounts are computed by code.** The tools return computed fields (last day
   to return, warranty end, days since delivery, fee) and a `facts` list in plain words.
   The model only reports them.
5. **Limits:**
   - at most `max_tool_calls` (6) tool calls per question
   - at the limit, one final answer with the tools removed
   - every answer generation (the first answer, that final call and the guard's one
     regeneration) counts towards v7's cap of 3
6. **The output guard in agent mode.**
   - **Allowed sources:** the passages `search_knowledge_base` returned this turn (their
     text, not its answer) and this turn's order-tool results. The tool results are
     trusted because our code computed them.
   - **Dates are checked too.**
   - **Kept from v7:** the seller-attribution and VoltCart-policy rules.
   - **On failure:** one regeneration, then a fixed reply that claims nothing.
7. **No side effects in v8.** A knowledge-base handoff inside the agent writes no ticket
   (`handoff_writes_ticket: false`; v6 and v7 keep the default `true` and behave as
   evaluated).

## Why a custom graph and not `create_react_agent`

The prebuilt ReAct agent would work. A custom graph makes the security-relevant parts
explicit and testable:

- the step limit, including cutting a reply that asks for more calls than are left
- the generation cap shared with the guard
- the guard as its own node
- the user bound into the tools

The graph is under 200 lines, and the unit tests drive it with a scripted fake model.

## Alternatives considered

- **Let the model compute dates from raw order fields.** Simpler tools, but models get
  "15 days from 20 October" wrong often enough to matter, and the guard couldn't tell a
  correct computed date from an invented one.
- **Pass the customer id as a tool argument "for flexibility".** One prompt injection away
  from reading someone else's orders. Isolation belongs in code.
- **A separate knowledge retriever for the agent.** It would duplicate v7's protections
  (keys, filters, sanitised collections, tagged sources, guard), and duplicates drift.
  Calling the v7 pipeline keeps one implementation.
- **Per-sentence citations from the agent (structured output).** Stricter for the guard,
  but more invalid-output retries. Deferred unless the red team shows the turn-level rule
  lets an attack through (it is run against v8 and compared with v7).

## Consequences

- **Cost:** every v8 question costs at least two large-model calls (decide, answer), plus
  the knowledge tool's v7 calls. On Groq's free tier a full golden-set run takes about two
  days.
- **Recording:** each results row records the agent's route: tools, tool calls, model
  calls, generations, whether it hit the step limit, and the outcome.
- **The golden set measures the knowledge tool through the agent.** Its recall and leak
  metrics come from the last knowledge call. The agent scenarios
  (`data/golden/agent_scenarios.jsonl`) measure tools, isolation and steps.

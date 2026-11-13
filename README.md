# RAG Support Assistant

[![CI](https://github.com/navendu10/rag-support-assistant/actions/workflows/ci.yml/badge.svg)](https://github.com/navendu10/rag-support-assistant/actions/workflows/ci.yml)

A customer-support assistant for **VoltCart**, a fictional online electronics retailer. It is
built in public, one session at a time, to show how retrieval-augmented generation (RAG)
systems actually break and how to fix them. The project deliberately reproduces seven
classic RAG failures: bad chunking, product-code confusion, poor ranking, vague queries
and wrong region, internal-document leaks, hallucination and prompt injection. Each one is
measured, fixed and measured again. The assistant then grows into a LangGraph agent that
can look up orders, check warranties and open tickets (with human approval for anything
that writes). Its tools are exposed through an MCP server, and it gets evaluation, tracing,
caching and a web UI. Everything runs on free and open-source tools.

**Write-up:** [Medium post (coming soon)](#)

## Architecture

> Placeholder: a diagram arrives once the first RAG pipeline exists. See
> [docs/architecture.md](docs/architecture.md).

**Stack:** LangChain + LangGraph · Weaviate (Docker) · FastEmbed `bge-small-en-v1.5` · Groq
LLMs · SQLite · MCP Python SDK · FastAPI · Streamlit

## Quick start (Windows 11, PowerShell)

Prerequisites: Python 3.12, Docker Desktop, Git, a free [Groq API key](https://console.groq.com/keys).

```powershell
git clone https://github.com/navendu10/rag-support-assistant.git
cd rag-support-assistant
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
pip install -r requirements-dev.txt
pre-commit install
Copy-Item .env.example .env        # then put your key in .env
docker compose up -d               # start Weaviate
python scripts/check_setup.py      # all checks should PASS
python scripts/generate_data.py    # build the fictional knowledge base
python scripts/ingest.py --stage v0
python scripts/ask.py --stage v0 --user cust_uk_01 "Can I return opened headphones?"
python scripts/chat.py --stage v4 --user cust_uk_01   # chat with history; shows each rewrite
python scripts/evaluate.py --stage v0   # ~5 min uncached (Groq free-tier limits)
pytest
```

Full details and troubleshooting: [docs/setup.md](docs/setup.md).

## Data

A deterministic, template-generated knowledge base of 53 documents: 8 product manuals,
regional return policies, store policies, 30 help-centre FAQs, 4 internal playbooks and
one marketplace listing. Each family is designed to trigger specific RAG failures. A
50-question golden set measures them. See [docs/data.md](docs/data.md).

## Results

Output of `python scripts/compare_stages.py` on the 50-question golden set (every stage is
scored on the same questions; Sessions 6 and 7 added g045-g050 to every stage; invented content
is judged for v4 on, so it is n/a for v0-v4a). Definitions are in [docs/evaluation.md](docs/evaluation.md).
Each stage has its own write-up:

- v0, the baseline: [00-baseline](docs/failures/00-baseline.md).
- v1, structure-aware chunking: [01-chunking](docs/failures/01-chunking.md). It fixes the
  chunks themselves, so no table is split any more, but end-to-end correctness is flat.
  The injection improvement is an accidental side effect, not a defence.
- v2, hybrid search: [02-hybrid-search](docs/failures/02-hybrid-search.md). It removes
  every competing product's chunk from the context of code questions (7/9 → 0/9). The
  headline numbers don't move, because v1 already answered those questions correctly.
- v3, cross-encoder reranking: [03-reranking](docs/failures/03-reranking.md).
  - Reranking alone raised correctness, but pushed three answers out of the top 5.
  - Blending the rerank and search scores keeps every answer v2 had, and takes
    correctness from 27 to 29 of 44.
  - A new LLM leak judge shows every customer leak question has leaked in every stage;
    the string check had missed them.
  - The model, fusion and alpha were tuned on this same golden set, so these numbers are
    optimistic.
- v4a and v4, query rewriting and region filters:
  [04-query-and-region](docs/failures/04-query-and-region.md).
  - Rewriting alone (v4a) resolves "it" from the chat history and adds category words, but
    correctness is flat. It won five answers and lost five, including an injection that
    now succeeds and two lost handoffs.
  - Adding the region filter from the user's profile (v4) takes correctness from 30 to 34
    of 47, and the vague and regional questions from 1 to 4 of 7.
  - The filter can't separate the outdated UK policy from the current one, so one stale
    answer got worse. It is personalisation, not access control.
- v5, access control: [05-access-control](docs/failures/05-access-control.md).
  - Internal playbooks live in their own Weaviate collection, and database roles let the
    customer key read only the public one. Weaviate refuses the request; it isn't filtered.
  - Customer retrieval leaks fall from 5/46 to 0/46, and judged answer leaks to 0/3.
    Agents keep their access.
  - Without the playbook, one leak question now **invents** a no-exceptions policy. That
    is for grounding (Session 8).
- v6a and v6, grounding and a corrective flow with human handoff:
  [06-grounding-and-handoff](docs/failures/06-grounding-and-handoff.md).
  - Grounding alone (v6a): role-aware prompts, numbered sources, cited structured output.
    Judged invented content falls from 9/50 to 1/50 (and that one is a judge mistake),
    every citation is valid, and expected handoffs rise from 1/7 to 6/7. Correctness drops
    from 39 to 33, because an honest "can't find it" with no offer of a person fails the
    reference, and the brief answers drop some conditions.
  - The LangGraph corrective flow (v6) retries once with a wider search, when retrieval
    scores low or the answer isn't found, then hands unanswered questions to a person (a
    ticket for customers). Handoffs reach 7/7 and correctness 39/50. Four of 43
    answerable questions are handed off (v5: two). One can't be answered by design, and
    three are retrieval misses that v5 had covered with invented answers. The retry
    rescued one (g048).

| Stage | Recall@5 | Hit@1 | MRR | Cand. recall@20 | Correctness | Retrieval leaks | Answer leaks | Answer leaks (judge) | Injection failures | Handoffs | Rewrite fallbacks | Agent internal retrieval | Agent internal access | False handoffs | Invented content | Citations valid |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| v0 | 40/45 (89%) | 26/45 (58%) | 0.69 | n/a | 29/50 (58%) | 5/46 (11%) | 1/3 (33%) | 3/3 (100%) | 2/3 (67%) | 1/7 (14%) | n/a | 3/4 (75%) | 2/4 (50%) | 3/43 (7%) | n/a | n/a |
| v1 | 38/45 (84%) | 24/45 (53%) | 0.65 | n/a | 29/50 (58%) | 4/46 (9%) | 0/3 (0%) | 3/3 (100%) | 0/3 (0%) | 1/7 (14%) | n/a | 3/4 (75%) | 1/4 (25%) | 4/43 (9%) | n/a | n/a |
| v2 | 38/45 (84%) | 24/45 (53%) | 0.65 | n/a | 29/50 (58%) | 4/46 (9%) | 0/3 (0%) | 3/3 (100%) | 0/3 (0%) | 2/7 (29%) | n/a | 3/4 (75%) | 1/4 (25%) | 4/43 (9%) | n/a | n/a |
| v3 | 40/45 (89%) | 27/45 (60%) | 0.71 | 44/45 (98%) | 31/50 (62%) | 4/46 (9%) | 0/3 (0%) | 3/3 (100%) | 0/3 (0%) | 2/7 (29%) | n/a | 3/4 (75%) | 1/4 (25%) | 3/43 (7%) | n/a | n/a |
| v4a | 41/45 (91%) | 30/45 (67%) | 0.75 | 44/45 (98%) | 31/50 (62%) | 4/46 (9%) | 0/3 (0%) | 1/3 (33%) | 1/3 (33%) | 0/7 (0%) | 0/50 (0%) | 3/4 (75%) | 2/4 (50%) | 4/43 (9%) | n/a | n/a |
| v4 | 42/45 (93%) | 31/45 (69%) | 0.80 | 44/45 (98%) | 35/50 (70%) | 5/46 (11%) | 0/3 (0%) | 2/3 (67%) | 1/3 (33%) | 1/7 (14%) | 0/50 (0%) | 3/4 (75%) | 2/4 (50%) | 3/43 (7%) | 9/50 (18%) | n/a |
| v5 | 42/45 (93%) | 33/45 (73%) | 0.83 | 45/45 (100%) | 39/50 (78%) | 0/46 (0%) | 0/3 (0%) | 0/3 (0%) | 1/3 (33%) | 1/7 (14%) | 0/50 (0%) | 3/4 (75%) | 2/4 (50%) | 2/43 (5%) | 9/50 (18%) | n/a |
| v6a | 42/45 (93%) | 33/45 (73%) | 0.83 | 45/45 (100%) | 33/50 (66%) | 0/46 (0%) | 0/3 (0%) | 0/3 (0%) | 0/3 (0%) | 6/7 (86%) | 0/50 (0%) | 3/4 (75%) | 1/4 (25%) | 5/43 (12%) | 1/50 (2%) | 50/50 (100%) |
| v6 | 42/45 (93%) | 33/45 (73%) | 0.82 | 45/45 (100%) | 39/50 (78%) | 0/46 (0%) | 0/3 (0%) | 0/3 (0%) | 0/3 (0%) | 7/7 (100%) | 0/50 (0%) | 4/4 (100%) | 2/4 (50%) | 4/43 (9%) | 2/50 (4%) | 50/50 (100%) |

Higher is better for recall, hit@1, MRR, candidate recall, correctness, handoffs and agent
internal access; lower is better for leaks, injection failures and rewrite fallbacks.
Retrieval and answer leaks count customers only. v0-v4 are read with the Weaviate admin key,
because they are the insecure "before" state (see [docs/security.md](docs/security.md)).

## Session status

| # | Session | Status |
|---|---------|--------|
| 0 | Foundations: repo, tooling, config, Weaviate, setup check | ✅ Done |
| 1 | Dummy knowledge base and golden test set | ✅ Done |
| 2 | v0 naive pipeline + evaluation harness (baseline) | ✅ Done |
| 3 | Fix 1: bad chunking (structure-aware chunking, stage v1) | ✅ Done |
| 4 | Fix 2: product-code confusion (hybrid search, stage v2) | ✅ Done |
| 5 | Fix 3: poor ranking (cross-encoder reranking, stage v3) | ✅ Done |
| 6 | Fix 4: vague queries and wrong region (query rewriting, region filter, stages v4a/v4) | ✅ Done |
| 7 | Fix 5: internal-document leaks (per-audience collections, Weaviate RBAC keys, stage v5) | ✅ Done |
| 8 | Fix 6: hallucination (grounded citations, LangGraph corrective flow with handoff, stages v6a/v6) | ✅ Done |
| 9 | Fix 7: prompt injection | ⏳ Planned |
| 10 | Agent tools: LangGraph agent with order lookup, warranty check and ticket creation | ⏳ Planned |
| 11 | Guardrails: human approval for write actions | ⏳ Planned |
| 12 | MCP server for the agent's tools | ⏳ Planned |
| 13 | Evaluation: held-out golden set, judge improvements | ⏳ Planned |
| 14 | Tracing and freshness (`effective_date`, stale documents) | ⏳ Planned |
| 15 | Caching and model routing | ⏳ Planned |
| 16 | FastAPI service, Streamlit UI and docs | ⏳ Planned |

Session titles after Session 1 are provisional. The session log is in
[docs/sessions.md](docs/sessions.md) and the failure catalogue is in
[docs/failures/](docs/failures/README.md).

## Known issues

- **Merged sections get the wrong context header**
  ([#6](https://github.com/navendu10/rag-support-assistant/issues/6)). When the v1+
  chunker merges a short section into the next one, the chunk's `[Title > Section]` header
  names only the later section. 42 of the 162 chunks are affected. One UK returns chunk
  headed "Refunds" starts with "Condition of returned items", and the header lowers its
  rerank score from 0.83 to 0.51 for g012. Fixing it changes chunking, so it means
  re-ingesting and re-evaluating v1-v3, and it is deferred.

## Licence

[MIT](LICENSE) © 2026 navendu10

# CLAUDE.md: rules for every session

## Project purpose

A public portfolio project: a RAG customer-support assistant for a **fictional online
electronics retailer**. Over 17 sessions it deliberately reproduces, measures and fixes
seven classic RAG failures (bad chunking, product-code confusion, poor ranking, vague
queries and wrong region, internal-document leaks, hallucination, prompt injection). It then
becomes a LangGraph agent with tools (order lookup, warranty check, ticket creation, with
human approval for write actions), exposes those tools through an MCP server, and finally
adds evaluation, tracing, caching and a UI. Code and docs are read by recruiters and
engineers, so clarity matters as much as correctness.

## Stack (free and open source only; do not swap without an ADR)

| Concern | Choice |
|---|---|
| RAG flow and agent | LangChain + LangGraph |
| Vector DB | Weaviate 1.x in Docker, Python client v4, **self-provided vectors** (no vectorizer modules) |
| Embeddings | FastEmbed `BAAI/bge-small-en-v1.5` (384 dims) |
| LLM | Groq via `langchain-groq` (model names in `configs/base.yaml`) |
| Dummy order data | SQLite |
| Tools protocol | Official MCP Python SDK + `langchain-mcp-adapters` |
| API / UI | FastAPI / Streamlit |
| Config | `pydantic-settings` + YAML + `.env` |
| Tooling | pip (no uv or poetry), ruff, pytest, pre-commit, GitHub Actions |

## Repo map

```
configs/base.yaml          shared config; configs/stages/ holds per-session overrides
data/generators/           deterministic templates for the knowledge base (import as `generators`;
                           facts.py is the single source of truth for every fact)
data/generated/            generator output + manifest.json (git-ignored; see docs/data.md)
data/golden/               hand-checked evaluation sets (committed)
data/redteam/              prompt-injection attacks for scripts/redteam.py (committed)
data/users.json            test users (committed)
src/rag_assistant/
  config.py                typed settings (YAML < .env < env vars)
  schemas.py               DocMetadata, User, GoldenItem models and loaders
  llm.py                   Groq models: throttle, retry on 429, SQLite cache (.cache/)
  pipeline.py              Pipeline.from_stage(name).answer(question, user, history)
  ingestion/               loaders (v0 text) + parsers (v1 Markdown), chunker (fixed) +
                           structure_chunker (headings, whole tables, context headers)
  retrieval/               vector (near_vector), hybrid (BM25 + vector, dynamic alpha),
                           codes (product/error code detection), factory (pick per stage),
                           rerank (FastEmbed cross-encoder over fetch_k candidates, fused
                           with the search score), rerank_input (reranker text variants),
                           filters (region filter from the user profile), access (Weaviate
                           key and collections by user role, RRF merge, SECURITY detector)
  query/                   rewrite (small-model query rewriter with history and catalogue,
                           validated JSON, retry then raw-message fallback), describe (CLI text)
  security/                access_setup (roles, database users, .env keys), probes (what a key
                           may read), sanitize (layer 1: trust levels, hidden HTML, instruction
                           flags), output_guard (layer 3: numbers and promises vs store
                           sources), redteam (attacks, layer configurations, scoring)
  generation/              naive (v0-v5 prompt), grounded (v6a on: role-aware prompt, numbered
                           sources, structured output, citation checks, "Sources:" list)
  graph/                   corrective (v6: LangGraph rewrite -> retrieve -> grade -> generate ->
                           check -> answer | handoff, with one wider retry on a low score or a
                           not-found answer), handoff (reasons, messages, tickets to
                           data/tickets/tickets.jsonl, git-ignored)
  eval/contexts.py         rebuild a stored run's context texts offline for back-filled judges
configs/stages/<stage>.yaml  StageConfig switches (all implemented since Session 9);
                           `index: Chunks_vN` reuses another stage's collection (ingest skips it);
                           `redteam_only` (v7raw) opens only for scripts/redteam.py
configs/prompts.yaml       all prompt templates (answer + judges)
results/<stage>/           committed full evaluation runs (results.jsonl, summary.json, report.md)
  ingestion/ retrieval/ query/ security/ generation/ graph/
  agent/ mcp_server/ cache/ eval/ api/ ui/
scripts/                   CLI entry points (check_setup.py, ...)
tests/unit/                fast, no network
tests/integration/         need Docker/Groq; mark with @pytest.mark.integration
tests/security/            access-control tests against Weaviate RBAC (integration + security)
docs/                      architecture, setup, sessions log, failures/, adr/
```

## Environment: Windows 11, PowerShell, Python 3.12 in `.venv`

Use PowerShell syntax (`;` not `&&` in Windows PowerShell 5.1; `$env:VAR`).
The tool shell may not have `.venv` activated (`python` can resolve to the global
interpreter). Call `.\.venv\Scripts\python.exe` explicitly when in doubt.

```powershell
.\.venv\Scripts\Activate.ps1                 # activate venv
pip install -e . ; pip install -r requirements-dev.txt
pre-commit install
docker compose up -d                          # start Weaviate
docker compose ps                             # check health
python scripts/check_setup.py                 # end-to-end environment check
python scripts/generate_data.py               # rebuild data/generated/ (deterministic)
python scripts/data_report.py                 # summary of docs and golden set
python scripts/setup_access.py --init-admin-key   # WEAVIATE_ADMIN_KEY in .env (before compose up)
python scripts/setup_access.py                # roles + customer/agent users, keys -> .env (safe to re-run)
python scripts/ingest.py --stage v0           # (re)build the stage's Weaviate collection (admin key)
python scripts/ask.py --stage v0 --user cust_uk_01 "question"
python scripts/ask.py --stage v4 --user cust_uk_01 --history-file turns.json "Can I return it?"
python scripts/chat.py --stage v4 --user cust_uk_01   # interactive, keeps history, shows rewrites
python scripts/show_rewrites.py --stage v4         # stored rewrites table -> results/v4/rewrites.md
python scripts/show_rewrites.py --stage v4 --live  # rewrite now (small model only), no answers
python scripts/check_rewrite_retrieval.py          # rewrite + retrieve vs stored runs, no answers
python scripts/evaluate.py --stage v0 [--tag leak] [--limit 5]   # results/<stage>/
python scripts/evaluate.py --stage v0 --only-missing   # answer new golden items only, merge
python scripts/evaluate.py --stage v6 --only-ids g011,g046   # re-answer chosen items, merge
python scripts/compare_stages.py [--by-tag]   # before/after table for README
python scripts/sweep_alpha.py --stage v2 [--alphas 0,0.5,1] [--fusion ranked]  # no LLM calls
python scripts/compare_rerankers.py [--models a,b]      # rerankers on v2 candidates, no LLM calls
python scripts/rerank_score_distribution.py --stage v3   # 0-1 rerank scores, min_score table
python scripts/sweep_rerank.py                     # reranker input x fusion x fetch_k x model
python scripts/evaluate.py --stage v0 --add-leak-judge  # LLM leak judge on stored answers
python scripts/evaluate.py --stage v4 --add-handoff-judge   # handoff judge on answerable rows
python scripts/evaluate.py --stage v4 --add-invented-judge  # invented-content judge, rebuilt contexts
python scripts/calibrate_invented_judge.py [--model large]  # invented judge vs hand labels
python scripts/ask.py --stage v6 --user cust_uk_01 "Is there a student discount?"  # handoff + ticket
python scripts/ingest.py --stage v7 ; python scripts/ingest.py --stage v7raw   # then setup_access.py
python scripts/redteam.py --stage v7 --layers 1,2,3   # also 1, 2, 3, none -> results/v7/redteam.md
python scripts/compare_rewrite_retrieval.py --stage v7 --rewrites-from v6   # no LLM calls
python scripts/show_chunks.py --stage v1 --doc returns-uk-2026-print [--grep text]
pytest -m integration                         # needs Weaviate + Groq (skipped by default)
pytest -m security                            # access-control tests against Weaviate RBAC (local)
ruff check . ; ruff format .                  # lint and format
pytest                                        # all tests
pytest tests/unit                             # fast tests only (what CI runs)
pytest -m "not integration"                   # skip tests needing services
docker compose down                           # stop (add -v to wipe data)
```

## Coding standards

- Type hints on every function signature; `from __future__ import annotations`.
- Docstrings (Google style) on every module, public class and function.
- Small, single-purpose functions; prefer pure functions that are easy to test.
- **Config over hard-coded values.** Model names, URLs, thresholds, top-k, chunk sizes and
  prompts belong in `configs/` and are read via `rag_assistant.config`.
- **Never commit secrets.** Keys live only in `.env` (git-ignored); use `SecretStr`; never
  print or log a secret value. Update `.env.example` when adding a variable.
- **Access is decided by role, at the database.** Customer code paths use the customer
  Weaviate key only; the admin key is for ingestion, `setup_access.py` and v0-v4. Every new
  feature that reads documents must respect roles (see `docs/security.md`).
- Keep pinned versions in `requirements*.txt`; verify current stable versions before adding.
- Ruff must pass (lint + format). No unused code or commented-out blocks.

## Definition of done for every change

1. **Tests**: every feature or fix has unit tests; integration tests where services are
   involved. Failure sessions add a test or eval that shows the failure, then the fix.
2. **Docs**: update the relevant `docs/` page. Each RAG failure gets
   `docs/failures/NN-name.md` (symptom, reproduction, root cause, fix, before/after
   numbers). Significant decisions get an ADR in `docs/adr/`.
3. **Every session**: add an entry to `CHANGELOG.md` (Keep a Changelog format) and
   `docs/sessions.md`, and update the README status table.
4. **Commits**: Conventional Commits (`feat:`, `fix:`, `docs:`, `test:`, `chore:`,
   `refactor:`, `ci:`). One branch per session: `session-NN-short-name`.

## How to work with me

- **Always plan first and wait for approval** before creating or changing files.
- Never commit or push unless explicitly asked.
- Run `ruff check .`, `ruff format --check .` and `pytest` before saying a task is done;
  report failures honestly.
- **Finish every session by explaining what you built in plain English**: what each new
  file is for, how the pieces fit, and how to run or verify it, plus a suggested
  conventional commit message.

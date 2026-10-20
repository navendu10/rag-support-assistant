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
data/users.json            test users (committed)
src/rag_assistant/
  config.py                typed settings (YAML < .env < env vars)
  schemas.py               DocMetadata, User, GoldenItem models and loaders
  ingestion/ retrieval/ query/ security/ generation/ graph/
  agent/ mcp_server/ cache/ eval/ api/ ui/
scripts/                   CLI entry points (check_setup.py, ...)
tests/unit/                fast, no network
tests/integration/         need Docker/Groq; mark with @pytest.mark.integration
docs/                      architecture, setup, sessions log, failures/, adr/
```

## Environment: Windows 11, PowerShell, Python 3.12 in `.venv`

Use PowerShell syntax (`;` not `&&` in Windows PowerShell 5.1; `$env:VAR`).

```powershell
.\.venv\Scripts\Activate.ps1                 # activate venv
pip install -e . ; pip install -r requirements-dev.txt
pre-commit install
docker compose up -d                          # start Weaviate
docker compose ps                             # check health
python scripts/check_setup.py                 # end-to-end environment check
python scripts/generate_data.py               # rebuild data/generated/ (deterministic)
python scripts/data_report.py                 # summary of docs and golden set
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

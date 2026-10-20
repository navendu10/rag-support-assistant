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
pytest
```

Full details and troubleshooting: [docs/setup.md](docs/setup.md).

## Data

A deterministic, template-generated knowledge base of 53 documents: 8 product manuals,
regional return policies, store policies, 30 help-centre FAQs, 4 internal playbooks and
one marketplace listing. Each family is designed to trigger specific RAG failures. A
40-question golden set measures them. See [docs/data.md](docs/data.md).

## Session status

| # | Session | Status |
|---|---------|--------|
| 0 | Foundations: repo, tooling, config, Weaviate, setup check | ✅ Done |
| 1 | Dummy knowledge base and golden test set | ✅ Done |
| 2 | Naive RAG baseline: reproduce all seven failures | ⏳ Planned |
| 3 | Fix 1: bad chunking | ⏳ Planned |
| 4 | Fix 2: product-code confusion (hybrid search) | ⏳ Planned |
| 5 | Fix 3: poor ranking (reranking) | ⏳ Planned |
| 6 | Fix 4: vague queries, wrong region and stale documents | ⏳ Planned |
| 7 | Fix 5: internal-document leaks (access control, Weaviate API keys) | ⏳ Planned |
| 8 | Fix 6: hallucination (grounding, citations, handoff) | ⏳ Planned |
| 9 | Fix 7: prompt injection | ⏳ Planned |
| 10 | LangGraph RAG flow | ⏳ Planned |
| 11 | Agent tools: order lookup and warranty check (SQLite) | ⏳ Planned |
| 12 | Ticket creation with human approval | ⏳ Planned |
| 13 | MCP server for the agent's tools | ⏳ Planned |
| 14 | Evaluation harness and golden sets | ⏳ Planned |
| 15 | Tracing and caching | ⏳ Planned |
| 16 | FastAPI service and Streamlit UI | ⏳ Planned |

Session titles after Session 1 are provisional. The session log is in
[docs/sessions.md](docs/sessions.md) and the failure catalogue is in
[docs/failures/](docs/failures/README.md).

## Licence

[MIT](LICENSE) © 2026 navendu10

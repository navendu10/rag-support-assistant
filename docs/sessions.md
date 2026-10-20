# Session log

One entry per session: goal, what was built, results and lessons.

## Session 0: Foundations (2026-10-16)

**Goal:** a reproducible, tested project skeleton with no RAG logic yet.

**Built**
- src-layout package `rag_assistant` with empty subpackages for every future component.
- Typed config (`config.py`): `configs/base.yaml` < `.env` < environment variables.
- Weaviate 1.40.1 in Docker Compose (no vectorizers, 1 GB limit, named volume).
- `scripts/check_setup.py`: PASS/FAIL checks for Python, Weaviate, FastEmbed (384-dim)
  and Groq.
- Unit tests for config; ruff and pre-commit; GitHub Actions CI; PR template.
- Docs: setup, architecture placeholder, failure index, ADRs 0001 and 0002.

**Model choices:** `openai/gpt-oss-20b` (small) and `openai/gpt-oss-120b` (large). The Llama
3.x models listed on Groq's docs page returned `404 model_not_found` for this account.
See [ADR 0002](adr/0002-groq-free-tier-llm.md).

**Next:** Session 1, dummy data and the golden test set.

## Session 1: Dummy data and golden test set (2026-10-16)

**Goal:** a realistic, deterministic knowledge base designed so every RAG failure appears
in Session 2, plus a golden set that measures them. No RAG code.

**Built**
- Template generators (no LLM, no randomness) for 53 documents:
  - 8 PDF manuals.
  - 7 return-policy documents across IN, UK and US, including an outdated 2025 UK policy.
  - Warranty, discounts and shipping policies.
  - 30 HTML FAQs.
  - 4 internal playbooks with a canary phrase.
  - A marketplace listing with a hidden prompt injection.
- Metadata on every document, plus `manifest.json`.
- `data/users.json` and a 40-item golden set validated by Pydantic.
- `scripts/data_report.py`; 63 new unit tests (72 total).

**Results**

| Measure | Value |
|---|---|
| Documents | 53 (49 public, 4 internal) |
| Golden questions | 40: chunking 4, product_code 4, ranking 3, query_region 4, leak 4, hallucination 4, injection 3, stale 2, normal 12 |
| Rebuild | byte-identical (SHA-256 test) |

**Design notes**
- Facts live in one module (`facts.py`). Tests restate them independently and check that
  conflicting phrases ("14 days", "lifetime", "30 seconds", goodwill amounts) appear only
  where intended.
- Returns tables list sealed rows before opened rows and start around character 820. The
  opened-audio row therefore lands past the 1,000-character mark, away from the header.

**Next:** Session 2, naive RAG baseline that reproduces all seven failures.

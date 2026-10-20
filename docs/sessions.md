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

## Session 2: v0 pipeline and evaluation harness (2026-10-20)

**Goal:** the deliberately naive v0 pipeline, a harness that scores any stage on the
golden set, baseline numbers, and real examples of each failure.

**Built**
- Stage switches validated by `StageConfig`; v0 has everything off.
- Ingestion into `Chunks_v0`: 117 chunks, 5 of them internal.
- Vector-only retrieval, the naive prompt and a `Pipeline` class.
- Evaluation with recall@5, a correctness judge, retrieval and answer leaks, injection,
  handoff and forbidden phrases.
- Throttle, retry and SQLite cache for Groq's free tier.
- `compare_stages.py`; 142 unit tests plus 2 integration tests.

**Results (v0)**

| Recall@5 | Correctness | Retrieval leaks | Answer leaks | Injection failures | Handoffs |
|---|---|---|---|---|---|
| 31/35 (89%) | 24/40 (60%) | 5/39 (13%) | 1/3 (33%) | 2/3 (67%) | 1/7 (14%) |

**What reproduced:**
- Ranking, wrong region, leaks, hallucination and injection reproduced clearly.
- Product-code confusion did **not** reproduce in answers. The wrong manual was retrieved,
  but the model read the model number.
- Chunking reproduced only **partly**. The splitter keeps the opened-audio row next to
  the header.
- Stale-document items failed through the region bug instead (India's 7 days, not the
  outdated 14). See [00-baseline](failures/00-baseline.md).

**Lessons**
- String checks must normalise LLM styling. The first scoring missed `£ 20` and
  `**lifetime warranty**`. The stored answers were rescored with no new LLM calls.
- Retrieval-leak counts matter. Two of three leak answers paraphrased a playbook without
  repeating the canary or any amount.
- v0 doesn't know today's date (g009 claimed a March 2026 TV was "27 months" old).

**Next:** Session 3, structure-aware chunking.

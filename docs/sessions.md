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

**Next:** Session 1, data generators and a naive RAG baseline.

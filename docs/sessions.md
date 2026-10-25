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

## Session 3: Structure-aware chunking (2026-10-24)

**Goal:** implement `chunking: structure` and evaluate it as stage v1, with everything
else identical to v0, so the comparison isolates the chunking fix.

**Built**
- `configs/stages/v1.yaml`: v0 plus `chunking: structure`, `chunk_size: 1200`,
  `chunk_overlap: 150` and `min_chunk_chars: 200`. It has its own `Chunks_v1` collection.
- Structure-preserving parsers:
  - PDF: `pymupdf4llm`, with page markers and removal of `**`, `<br>` and `<u>`.
  - HTML: `markdownify`, with hidden text kept.
  - Markdown: as is.
- Structure-aware chunker:
  - Splits on h1-h3 headings and never cuts a table (tables are runs of `|` lines).
  - Sub-splits long prose and merges short sections.
  - Adds `[Title > Section]` headers; every chunk inherits all metadata plus `section`
    and `page`.
- `scripts/show_chunks.py`, which prints whatever fields a chunk has, for v0 and v1.
  `compare_stages.py --by-tag`.
- ADR 0003 (pymupdf4llm, AGPL; pinned to 1.28.2 because the 2.0 releases of
  `pymupdf4llm` and `pymupdf-layout` are empty placeholders).
- 174 unit tests.

**Results**

| Stage | Recall@5 | Correctness | Retrieval leaks | Answer leaks | Injection failures | Handoffs |
|---|---|---|---|---|---|---|
| v0 | 31/35 (89%) | 24/40 (60%) | 5/39 (13%) | 1/3 (33%) | 2/3 (67%) | 1/7 (14%) |
| v1 | 30/35 (86%) | 23/40 (58%) | 4/39 (10%) | 0/3 (0%) | 0/3 (0%) | 1/7 (14%) |

**What happened** (details in [01-chunking](failures/01-chunking.md))
- **Chunk level: fixed.**
  - 0 of 7 return-window tables are split, against 7 of 7 in v0.
  - All 162 chunks have a context header, and all 91 PDF chunks have a page.
- **End to end: flat.** Small prose sections ("Refunds") now beat the whole-table chunk
  for vague return questions. When tables do win, near-identical regional tables crowd
  the top 4.
  - query_region recall fell 4/4 → 2/4.
  - chunking-tag forbidden hits rose 0/4 → 2/4. For example, g003 told an Indian
    customer the UK's 30 days.
- **Injection failures 2/3 → 0/3, by accident.** The hidden instruction still reaches the
  model, but it now sits beside a clearly stated 6-month warranty. This isn't a defence;
  Session 9 is still needed.
- **Judge noise.** g037 was marked INCORRECT although it matches its reference. g021 and
  g031 were marked down for omitting details from long reference answers.

**Lessons**
- Keeping tables whole makes regional variants into near-duplicate chunks. Chunking and
  region handling interact.
- Large table chunks embed as one diluted vector. That isn't truncation: the largest is
  461 of bge-small's 512 tokens.
- Always check the latest PyPI release before pinning it: here, "latest" was an empty
  package.
- The tool's PowerShell may not use `.venv`. Call `.venv\Scripts\python.exe` explicitly.

**Next:** Session 4, hybrid search (BM25 + vectors) for product codes and exact terms.

## Session 4: Hybrid search for product and error codes (2026-10-25)

**Goal:** implement `search: hybrid` and evaluate it as stage v2, identical to v1 except
for the search settings. Also strengthen the evaluation so ranking changes are visible.

**Built**
- Evaluation:
  - Hit@1 and MRR for every stage. They are computed from stored doc_ids, so v0 and v1 got
    them via `--rescore`.
  - Four new product_code golden items (g041-g044, 44 in total).
  - `evaluate.py --only-missing` brought v0 and v1 to 44 questions with 8 new LLM answers,
    keeping every stored latency.
- `retrieval/codes.py` detects the manifest's product codes, with or without hyphens,
  plus `E-NN` error codes. It ignores "Wi-Fi 6", years and product names.
- `retrieval/hybrid.py`: Weaviate `query.hybrid` with an explicit FastEmbed vector, dynamic
  alpha and canonicalised codes in the keyword text. `retrieval/factory.py` picks vector or
  hybrid per stage.
- `HybridConfig` (nested `hybrid:` block) with validation. Explicit Weaviate tokenization:
  `word` for product_code, `field` for region and audience.
- `scripts/sweep_alpha.py` writes `results/v2/alpha_sweep.md`. It needs no LLM calls.
- 242 unit tests and 4 integration tests.

**Sweep and choice** (details in [02-hybrid-search](failures/02-hybrid-search.md))
- For the 9 questions with a code, recall, hit@1 and MRR are identical at every alpha,
  because v1 already ranks the right chunk first. What changes is the context: expected-doc
  chunks in the top 4 go from 26/36 (vector) to 34/36 (alpha 0.25).
- For the 30 questions without a code, keyword weight hurts. At alpha 0 recall falls
  25 → 19 of 30. At 0.75, g012, g017 and g020 lose their whole answer context.
- Chosen: dynamic, `code_alpha: 0.25`, `default_alpha: 1.0`, `relative_score`.

**Results**

| Stage | Recall@5 | Hit@1 | MRR | Correctness | Retrieval leaks | Answer leaks | Injection failures | Handoffs |
|---|---|---|---|---|---|---|---|---|
| v0 | 35/39 (90%) | 24/39 (62%) | 0.72 | 28/44 (64%) | 5/43 (12%) | 1/3 (33%) | 2/3 (67%) | 1/7 (14%) |
| v1 | 34/39 (87%) | 22/39 (56%) | 0.68 | 27/44 (61%) | 4/43 (9%) | 0/3 (0%) | 0/3 (0%) | 1/7 (14%) |
| v2 | 34/39 (87%) | 22/39 (56%) | 0.68 | 27/44 (61%) | 4/43 (9%) | 0/3 (0%) | 0/3 (0%) | 2/7 (29%) |

**What happened**
- **Retrieval: fixed for codes.** In v1, 7 of 9 coded questions had another product's
  chunk in context, for example the XR-2000 reset steps for "How do I factory reset the
  XR-2200?". In v2, none do.
- **End to end: flat.** No per-tag count moved by more than one question. v1 already
  answered every coded question correctly.
- **g023 handoff ANSWERED → HANDOFF.** v1 invented a "no price-matching" policy; v2 handed
  off. That's one item, likely answer variance.
- **query_region and stale MRR moved (0.33 → 0.38, 0.42 → 0.33)** without any alpha
  change. The UK 2026 Markdown, its PDF twin and the 2025 PDF score identically, and
  hybrid search breaks the ties in a different order.
- **Hit@1 shows Session 3's ranking regression:** v0 24/39 → v1 22/39. For g012 and g015,
  a short FAQ now outranks the regional policy.

**Lessons**
- Recall@k can't see ranking or context contamination; hit@1, MRR and a context-level
  check can.
- A strong generator hides retrieval bugs. Correct answers don't mean clean context.
- Keyword search is a per-question tool. A single global alpha trades code questions
  against vague ones.
- v2's total latency is mostly cache hits (prompts unchanged since v1). Compare retrieval
  latency only: about 11 ms for both.

**Next:** Session 5, reranking, for questions whose right document is retrieved but
ranked low (g009-g011).

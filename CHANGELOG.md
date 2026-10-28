# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.6.0] - 2026-10-28 (Session 5: cross-encoder reranking)

### Added

- Stage v3 (`configs/stages/v3.yaml`): v2 plus `rerank: true`.
  - Model `Xenova/ms-marco-MiniLM-L-6-v2`, with `fetch_k: 20`.
  - `rerank_fusion: blend` with `rerank_blend_weight: 0.5`: 0.5 × sigmoid(logit) plus
    0.5 × the normalised search score.
  - `min_score: null`, and it searches v2's collection (`index: Chunks_v2`).
- Reranker (`retrieval/rerank.py`):
  - A FastEmbed `TextCrossEncoder`, loaded once and warmed up.
  - Orderings `replace` / `rrf` / `blend`.
  - Every chunk keeps the raw logit and a 0-1 `rerank_score`.
  - The scorer can be injected for tests.
- Reranker input variants (`retrieval/rerank_input.py`): no header, linearised tables
  and 4-row table groups. They are implemented for the sweep; v3 uses the chunk as stored.
- Stage settings `rerank_model`, `fetch_k`, `min_score`, `rerank_fusion`,
  `rerank_blend_weight`, and `index` for reusing another stage's collection. `load_stage`
  checks that the owning stage exists and has identical ingestion settings.
- Evaluation:
  - Candidate recall@20 and a per-question miss type (`search miss` / `ranking miss` /
    `in context`).
  - Per-step latency (retrieve, rerank, generation) as mean and p95. Generation excludes
    throttle and retry waits and counts uncached calls only.
  - An **LLM leak judge** (`judge_leak`) for customer leak items, kept beside the string
    check, with `evaluate.py --add-leak-judge` to back-fill stored runs.
- Scripts (none makes LLM calls):
  - `compare_rerankers.py` → `results/v3/reranker_comparison.md`
  - `rerank_score_distribution.py` → `results/v3/score_distribution.md`
  - `sweep_rerank.py` → `results/v3/rerank_sweep.md`
- Tests:
  - Unit tests for reranking, fusion, input variants, the sweep helpers, config
    validation, index reuse, the ingest skip, wait accounting, latency summaries and the
    leak judge.
  - An integration test that the TV-lines warranty chunk is in v3's context.
- `results/v3/`, `docs/failures/03-reranking.md` and `docs/adr/0004-reranker-model.md`.
- [Issue #6](https://github.com/navendu10/rag-support-assistant/issues/6): merged
  sections get the wrong context header (42 of 162 chunks). Logged, not fixed.

### Changed

- `rerank` is no longer a "not implemented" switch. `min_score` is (Session 8).
- `ingest.py` skips stages that reuse another stage's index, and `ingest()` refuses them.
- `llm.ThrottledTransport` counts HTTP attempts and wait seconds per model, and
  `llm.timed_calls` turns them into per-call timings.
- `Pipeline` records `retrieve`, `rerank`, `generate` (excluding waits), `generate_wait` and
  `generate_requests`.
- v0-v2 were back-filled with the leak judge from their stored answers: 3/3 customer leaks
  in every stage, against 0-1/3 found by the string check.
  - **Their reported leak rate rose because the metric improved, not because anything
    regressed.** No answer was regenerated, and the string-check figures are unchanged.
    The new judge catches paraphrased leaks the string check missed, e.g. g019's
    description of the internal fraud checks.
  - The reports' total-latency row is relabelled "wall clock, throttle waits and cache
    hits included". It was re-rendered from the stored `summary.json`, and no value
    changed.

### Fixed

- `eval.sweep.score_group` cuts runs to the top 5 before scoring. Runs holding all 20
  reranker candidates had been scored as recall@20. Session 4's sweep was unaffected.

## [0.5.0] - 2026-10-25 (Session 4: hybrid search)

### Added

- Stage v2 (`configs/stages/v2.yaml`): v1 plus `search: hybrid`, ingested into
  `Chunks_v2`.
  - It uses dynamic alpha: 0.25 when the question contains a product or error code, and
    1.0 (pure vector) otherwise.
  - Fusion is `relative_score`, over `text`, `title` and `product_code^2`.
- Hybrid retrieval (`retrieval/hybrid.py`):
  - Calls Weaviate `query.hybrid` with our FastEmbed vector passed explicitly.
  - Keyword text has canonicalised codes ("xr2200" -> "XR-2200").
- Code detection (`retrieval/codes.py`): one pattern per manifest product code, with
  optional hyphens, plus `E-NN` error codes. `retrieval/factory.py` picks the retriever
  for a stage.
- `HybridConfig` stage settings: `alpha_strategy`, `alpha`, `code_alpha`, `default_alpha`,
  `fusion` and `query_properties` with optional `^N` boosts, all validated.
- `scripts/sweep_alpha.py` (retrieval only):
  - Reports recall@5, hit@1, MRR and a context diagnostic per question group, for every
    fixed alpha and dynamic pair.
  - Writes `results/v2/alpha_sweep.md`.
- Evaluation:
  - Hit@1 and MRR for every stage, in `results.jsonl`, `summary.json`, `report.md` and
    `compare_stages.py`.
  - `report.md` gains a rank column.
  - `evaluate.py --only-missing` answers only new golden items and merges them, keeping
    stored latencies.
- Golden set: four product_code items (g041-g044). That makes 44 items, 8 of them
  product_code.
- Tests:
  - Unit tests for code detection, alpha selection, query planning, hybrid config,
    hit@1/MRR, the sweep and `--only-missing` merging.
  - An integration test that the XR-2200 manual ranks first in v2 and the XR-2000 manual
    stays out of its context.
- `results/v2/` and `docs/failures/02-hybrid-search.md`.

### Changed

- `StageConfig`: a nested `hybrid:` block replaces the top-level `alpha`. `search` is no
  longer a "not implemented" switch.
- Weaviate properties now declare tokenization: `word` for text, title, section and
  product_code, and `field` for doc_id, region, audience, doc_type and effective_date.
- `compare_stages.py --by-tag` prints one table per metric (tags down, stages across).
- v0 and v1 results were extended to the 44-question set and rescored with hit@1 and MRR.
  No old answer, verdict or latency changed.
- The router speeds and the PB-20K USB-C output moved into `generators/facts.py`. The
  generated files are byte-identical.

## [0.4.0] - 2026-10-24 (Session 3: structure-aware chunking)

### Added

- Stage v1 (`configs/stages/v1.yaml`): v0 plus `chunking: structure`, ingested into its own
  `Chunks_v1` collection.
- Structure-preserving parsers (`ingestion/parsers.py`):
  - PDF: `pymupdf4llm` with page markers and artefact clean-up.
  - HTML: `markdownify`, keeping hidden text as v0 did.
  - Markdown: as is.
- Structure-aware chunker (`ingestion/structure_chunker.py`):
  - Splits on h1-h3 headings and never cuts a table; tables are found as runs of `|`
    lines.
  - Sub-splits long prose (1200 max, 150 overlap) and merges sections under 200
    characters.
  - Adds `[Title > Section > Subsection]` context headers and full metadata inheritance,
    including `section` and `page`.
- `scripts/show_chunks.py --stage --doc [--grep]` and `compare_stages.py --by-tag`.
- `min_chunk_chars` stage setting; `product_code`, `effective_date`, `section` and `page`
  on every chunk (stored in Weaviate for evaluation; not used for retrieval).
- Unit tests for parsing, chunking rules, bounds, metadata, determinism and
  `show_chunks`.
- `results/v1/`, `docs/failures/01-chunking.md`,
  `docs/adr/0003-pymupdf4llm-for-pdf-parsing.md`.

### Changed

- `chunking` is no longer a "not implemented" switch.
- `ingestion.pipeline.chunk_documents()` is shared by ingestion and `show_chunks`.
  v0 output is unchanged (117 chunks).
- New dependencies: `pymupdf4llm`, `pymupdf` and `pymupdf-layout` 1.28.2 (AGPL-3.0; the
  2.0 releases are empty placeholders), and `markdownify` 1.2.3.

## [0.3.0] - 2026-10-20 (Session 2: v0 pipeline and evaluation harness)

### Added

- `StageConfig` with explicit switches (`configs/stages/v0.yaml`). Switches that are not
  implemented yet raise `NotImplementedError` naming the session that adds them.
- Ingestion: pypdf, BeautifulSoup and Markdown loaders; `RecursiveCharacterTextSplitter`
  (1000/100); FastEmbed vectors; one Weaviate collection per stage (`Chunks_v0`) holding
  public and internal documents (`scripts/ingest.py`).
- Vector-only retrieval, a naive generation prompt (`configs/prompts.yaml`) and a
  `Pipeline` class with `answer(question, user, history)` (`scripts/ask.py`).
- Evaluation harness (`scripts/evaluate.py`), reporting:
  - Recall@5 and LLM-judge correctness.
  - Retrieval and answer leaks.
  - Injection failures and the handoff judge.
  - Forbidden phrases and latency.
  - Outputs: `results.jsonl`, `summary.json` and `report.md`. `--rescore` re-applies the
    metrics to stored answers.
- `scripts/compare_stages.py` and the README Results section.
- `rag_assistant.llm`: per-model request and token throttle, retry on 429/5xx honouring
  `retry-after`, and LangChain `SQLiteCache` in `.cache/`.
- Baseline results in `results/v0/`; `docs/failures/00-baseline.md`; `docs/evaluation.md`.
- Unit tests for every metric, stage config, loaders, splitter, rate limiter and retry;
  integration tests (`pytest -m integration`).

### Changed

- Golden set: `must_not_contain` now holds specific claims, never single words (for
  example "has a lifetime warranty" instead of "lifetime"). Leak items also forbid the
  internal credit amounts.
- `docs/data.md`: corrected the chunking description. The recursive splitter keeps the
  UK and US opened-audio row next to the table header.
- New dependencies: `beautifulsoup4` 4.15.0, `langchain-community` 0.4.2 and
  `langchain-text-splitters` 1.1.3. pytest skips integration tests by default.

## [0.2.0] - 2026-10-16 (Session 1: Dummy data and golden test set)

### Added

- Deterministic knowledge-base generators in `data/generators/` (manuals, return policies,
  warranty/discounts/shipping policies, FAQs, internal playbooks, marketplace listing),
  run by `scripts/generate_data.py`. They write 53 documents and `manifest.json` to
  `data/generated/`.
- Per-document metadata (YAML front matter, HTML meta tags or PDF `.meta.json` sidecars).
- `rag_assistant.schemas` with `DocMetadata`, `User`, `Turn` and `GoldenItem` models and
  loaders.
- `data/users.json` (three customers, one agent) and `data/golden/questions.jsonl`
  (40 items across nine failure tags).
- `scripts/data_report.py` summarising documents and golden questions.
- Unit tests for determinism, facts, phrase exclusivity, canaries, metadata, PDF
  extraction and the golden set.
- `docs/data.md`.

### Changed

- Added `reportlab` 5.0.1 and `pypdf` 6.21.0 to `requirements.txt`.
- `pyproject.toml`: `data/` added to the pytest path and to ruff's first-party sources.
- README session plan renumbered: Session 2 is the naive baseline, and Sessions 3-9 fix
  one failure each.

## [0.1.0] - 2026-10-16 (Session 0: Foundations)

### Added

- src-layout package `rag_assistant` with placeholder subpackages (ingestion, retrieval,
  query, security, generation, graph, agent, mcp_server, cache, eval, api, ui).
- Typed settings in `rag_assistant.config`, loaded from `configs/base.yaml`, `.env` and
  environment variables.
- Docker Compose service for Weaviate 1.40.1 (no vectorizer modules, 1 GB limit, named volume).
- `scripts/check_setup.py`, which checks Python, Weaviate, FastEmbed and Groq.
- Unit tests for configuration.
- Pinned `requirements.txt` / `requirements-dev.txt` and a `pyproject.toml` with ruff
  and pytest settings.
- pre-commit hooks, GitHub Actions CI and a pull request template.
- `CLAUDE.md`, `README.md`, `.env.example` and docs (setup, architecture placeholder,
  session log, failure catalogue, ADRs 0001 and 0002).

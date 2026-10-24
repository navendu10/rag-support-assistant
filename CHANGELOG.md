# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

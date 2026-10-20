# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

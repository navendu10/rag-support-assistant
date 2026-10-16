# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

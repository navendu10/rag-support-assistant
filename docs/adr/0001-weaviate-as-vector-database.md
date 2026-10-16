# ADR 0001: Weaviate as the vector database

- **Status:** Accepted
- **Date:** 2026-10-16

## Context

The assistant needs a vector store that runs locally for free, supports filtering on
metadata (region, product, access level), and offers keyword + vector **hybrid search**.
Hybrid search is the planned fix for product-code confusion (Failure 2). The store should
also behave like a real production database so the project is a credible portfolio
piece, and it must run on a Windows laptop through Docker Desktop.

## Decision

Use **Weaviate 1.x** (pinned, currently 1.40.1) in Docker Compose with the **Python client
v4**. We compute embeddings ourselves with FastEmbed and supply the vectors ("bring your
own vectors"), so no vectorizer modules are enabled. Anonymous access is on for now; API-key
authentication comes in Session 7.

## Consequences

- Positive: built-in BM25 + vector hybrid search, rich filters and multi-tenancy, with no
  extra services.
- Positive: self-provided vectors keep the embedding model under our control and swappable.
- Positive: same API locally and in a hosted cluster.
- Negative: a separate container to run. Docker Desktop must be up for integration tests
  and `check_setup.py`; CI unit tests do not need it.
- Negative: memory use grows with data; capped at 1 GB for local work.
- Negative: we must keep the collection's vector dimension (384) in sync with the
  embedding model.

## Alternatives considered

| Option | Why not chosen |
|---|---|
| Chroma | Very easy, but weaker hybrid search and filtering; feels less like production |
| Qdrant | Strong choice; Weaviate preferred for its native BM25 hybrid search |
| pgvector (Postgres) | Good for SQL-heavy apps; hybrid search needs manual work |
| FAISS | A library, not a database: no persistence API, filtering or server |
| Pinecone | Managed and paid beyond the free tier; not open source |

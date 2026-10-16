# Architecture

> Placeholder. This page is filled in from Session 1, when the first pipeline exists.

## Planned components

| Package (`src/rag_assistant/`) | Responsibility |
|---|---|
| `ingestion` | Load, clean, chunk and embed documents; write them to Weaviate |
| `retrieval` | Vector / hybrid search and reranking |
| `query` | Query rewriting, clarification, region detection |
| `security` | Document access control, leak prevention, prompt-injection defences |
| `generation` | Prompt building and grounded, cited answers |
| `graph` | LangGraph flow tying the steps together |
| `agent` | Tool-using agent with human approval for write actions |
| `mcp_server` | MCP server exposing the agent's tools |
| `cache` | Embedding, retrieval and response caches |
| `eval` | Golden-set evaluation and metrics |
| `api` / `ui` | FastAPI service and Streamlit front end |

## Data flow (target)

```
user question -> query (rewrite / region) -> retrieval (Weaviate + rerank)
              -> security (filter) -> generation (Groq) -> answer + citations
```

# ADR 0002: Groq free tier for LLM inference

- **Status:** Accepted
- **Date:** 2026-10-16

## Context

The project must cost nothing to run, respond fast enough for interactive demos and
repeated evaluation runs, support tool calling for the agent sessions, and integrate
cleanly with LangChain/LangGraph. Running models locally on a laptop without a strong GPU
is slow and would make evaluation runs painful.

## Decision

Use **Groq** through `langchain-groq`, with two production-tier models set in
`configs/base.yaml`:

- **Small / fast:** `openai/gpt-oss-20b` for routing, query rewriting, classification
  and cheap checks.
- **Large:** `openai/gpt-oss-120b` for final answers and agent tool calling.

Both are open-weight models with a 131k-token context and native tool calling. Model
names are config, not code, so either can be swapped without code changes.

## Consequences

- Positive: free, and very low latency, which keeps evaluation loops quick.
- Positive: OpenAI-style chat and tool calling, well supported by LangChain.
- Negative: gpt-oss models are reasoning models. They spend tokens on hidden reasoning
  (returned separately from the answer), so answers cost more tokens than a plain chat
  model. A lower `reasoning_effort` can reduce this.
- Negative: free-tier **rate limits** (requests and tokens per minute/day). Evaluation
  code needs retries, backoff and caching.
- Negative: model availability varies by account and changes over time. The Llama 3.x
  models on Groq's public docs page returned `404 model_not_found` for this account, so
  `scripts/check_setup.py` verifies the configured model on every setup.
- Negative: prompts leave the machine. Fine for fictional data, but worth noting for real use.

## Alternatives considered

| Option | Why not chosen |
|---|---|
| `llama-3.1-8b-instant` / `llama-3.3-70b-versatile` on Groq | First choice (plain chat, no reasoning tokens), but not available to this account |
| `qwen/qwen3.8-27b` on Groq | Preview model; may be discontinued at short notice |
| Ollama (local models) | Free and private, but slow on CPU-only laptops; may be added later as an offline option |
| OpenAI / Anthropic APIs | Higher quality, but paid; conflicts with the free-only constraint |
| Hugging Face Inference API | Free tier is slower and less predictable for tool calling |

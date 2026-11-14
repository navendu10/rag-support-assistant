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

## Session 5: Cross-encoder reranking (2026-10-28)

**Goal:** implement `rerank` and evaluate it as stage v3, identical to v2 except for
reranking. `top_k` stays 4, so only which chunks reach the LLM, and their order, change.

**Built**
- `retrieval/rerank.py`: a FastEmbed cross-encoder (MiniLM-L-6) over 20 hybrid-search
  candidates.
  - Raw logits plus sigmoid 0-1 scores.
  - Orderings `replace` / `rrf` / `blend`; v3 uses `blend`, 0.5/0.5.
- `retrieval/rerank_input.py`: header-free, linearised-table and row-group inputs, used by
  the sweep.
- `index: Chunks_v2`, so v3 reuses v2's collection; `ingest.py --stage v3` explains and
  skips.
- Evaluation:
  - Candidate recall@20 and per-question miss types.
  - Per-step latency excluding throttle waits.
  - An LLM leak judge, back-filled for v0-v2.
- Scripts: `compare_rerankers.py`, `rerank_score_distribution.py` and `sweep_rerank.py`
  (no LLM calls).
- ADR 0004, plus issue #6 for a chunk-header bug.
- 304 unit tests and 5 integration tests.

**How it went**
1. **Rerank-only** (the first v3 run) raised hit@1 from 22 to 26 and correctness from 27
   to 31, but pushed g012, g017 and g020 out of the top 5, and put the twin router manuals
   back into 5 of 9 code questions' context.
2. **Diagnosis** of the six recall misses:
   - **Tables:** MiniLM scores them near 0 (g002, g017; g011's generic warranty row).
   - **A vague question:** g012.
   - **Truncation:** g015, at 517 tokens.
   - **A correctly relevant internal playbook:** g020.
   - **For codes, the keyword signal is lost.** g012's chunk header was also wrong, which
     led to issue #6 (26% of chunks affected).
3. **Sweep** of input × fusion × fetch_k × model. Fusion mattered and the input didn't.
   Linearised tables and row groups helped g017 under rerank-only, but cost other
   questions. bge and jina weren't better, and FastEmbed caps every reranker at 512
   tokens.
4. **Chosen:** `chunk, blend 0.5, fetch_k 20`. No question loses recall@5 or hit@1
   against v2.

**Results**

| Stage | Recall@5 | Hit@1 | MRR | Cand. recall@20 | Correctness | Retrieval leaks | Answer leaks | Answer leaks (judge) | Injection failures | Handoffs |
|---|---|---|---|---|---|---|---|---|---|---|
| v0 | 35/39 (90%) | 24/39 (62%) | 0.72 | n/a | 28/44 (64%) | 5/43 (12%) | 1/3 (33%) | 3/3 (100%) | 2/3 (67%) | 1/7 (14%) |
| v1 | 34/39 (87%) | 22/39 (56%) | 0.68 | n/a | 27/44 (61%) | 4/43 (9%) | 0/3 (0%) | 3/3 (100%) | 0/3 (0%) | 1/7 (14%) |
| v2 | 34/39 (87%) | 22/39 (56%) | 0.68 | n/a | 27/44 (61%) | 4/43 (9%) | 0/3 (0%) | 3/3 (100%) | 0/3 (0%) | 2/7 (29%) |
| v3 | 36/39 (92%) | 24/39 (62%) | 0.73 | 39/39 (100%) | 29/44 (66%) | 4/43 (9%) | 0/3 (0%) | 3/3 (100%) | 0/3 (0%) | 2/7 (29%) |

**What happened** (details in [03-reranking](failures/03-reranking.md))
- **Correctness, v2 vs v3: 27/44 → 29/44.** Nothing got worse.
  - g032 is a real ranking win: the first-order discount FAQ moved from rank 7 to 2.
  - g037 is mostly judge variance.
- **Recall@5 34 → 36** (g013, g032); **hit@1 22 → 24**, keeping only half of rerank-only's
  gain.
- **Still wrong (15):** 3 ranking misses (g002, g011, g015), 10 with the evidence in context
  (mostly region and stale confusion, Session 6), and 2 with no expected doc. There are
  no search misses.
- **Leaks were under-counted.** The LLM judge finds 3/3 customer leaks in every stage, v0 to
  v3. g019 described internal fraud checks every time, and the string check never matched.
  The reranker scores the playbooks highly because they are relevant; Session 7 must filter
  first.
- **Codes:** 4 of 9 coded questions are still contaminated (0/9 in v2).
- **Latency:** search 21 ms, rerank 1.1 s mean / 2.0 s p95, uncached generation 1.8 s mean.
  The earlier 39 s p95 was Groq throttle waits.

**Lessons**
- Similarity is not relevance, but a cross-encoder's relevance isn't the whole story
  either. Replacing the search order discards real evidence (exact codes, regional
  tables), and fusing the two kept the gains without the losses.
- Check per question, not just totals. A configuration that gains two answers and loses
  one looks like a win in a count.
- A string match can't detect a paraphrased leak. Keep it, but add a judge, and spot-check
  the judge.
- Measure what you mean: wall-clock latency on a rate-limited free tier mostly measures
  waiting.
- My own analysis code had two bugs: recall@20 was scored as recall@5, and error-code
  questions were counted as contaminated. Both were caught by checking a baseline row
  against a known result.
- Alpha, the reranker, the fusion and fetch_k were all tuned on the scoring set, so the
  numbers are optimistic.

**Next:** Session 6, query rewriting with chat history, and region and date filters, for
g003, g012-g016 (and a `product_code` filter for the remaining code contamination).

## Session 6: Query rewriting and region filters (2026-11-01)

**Goal:** fix failure #4, vague questions and the wrong region, in two separately measured
stages: v4a = v3 + query rewriting with chat history, and v4 = v4a + a region filter from
the user profile.

**Built**
- `query/rewrite.py`: a small-model rewriter that sees the history, a product catalogue
  and the message.
  - Validated JSON output: standalone question, search query, codes, category. There is
    no region field.
  - Retry once, then fall back to the raw message.
- `retrieval/filters.py`: `region in [profile region, ALL]` for customers and no filter for
  agents, applied inside the Weaviate query.
- The manifest gains a product catalogue (code, name, return-table category).
- `scripts/chat.py`, `ask.py --history-file` and `scripts/show_rewrites.py`.
- Evaluation stores each rewrite and filter, adds no-LLM rewrite checks, and reports
  rewrite latency and fallbacks.
- Golden items g045-g047.

**How it went**
1. **The first prompt made retrieval worse.** "Keyword-rich with product names and
   codes" sent returns questions to the product manuals (recall@5 38 → 31 in a
   retrieval-only check), and turned "headphones" into "AeroBuds Pro".
2. **The prompt was revised once:**
   - Policy questions get the category and policy terms, not the product name.
   - A generic word gets a category but no code.
   - Recall@5 went to 39, or 40 with the filter.
3. Full runs of v4a and v4, then the question-by-question attribution below.
4. **A composed `search_query` was tried and reverted.**
   - **The idea:** the question's words plus catalogue names and codes, so that no
     product could be dropped (g027, g023).
   - **The result:** in a retrieval-only check, recall@5 fell to 30, because every
     policy question that names a product returned only that product's manual.
   - **Option 3** (a second search restricted to policies and FAQs, with the same
     filters) reached 38/39, below the pass rule of 39/40.
   - **The revert:** the prompt and schema were restored exactly; all 47 rewrites
     reproduce from the cache.
   - **What stayed:** a `dropped product` check and the cheap
     `check_rewrite_retrieval.py`. All of this was tuned on the evaluation set.

**Results** (47 questions; v0-v3 answered only g045-g047 this session)

| Stage | Recall@5 | Hit@1 | MRR | Cand. recall@20 | Correctness | Retrieval leaks | Answer leaks | Answer leaks (judge) | Injection failures | Handoffs | Rewrite fallbacks |
|---|---|---|---|---|---|---|---|---|---|---|---|
| v0 | 38/42 (90%) | 24/42 (57%) | 0.70 | n/a | 28/47 (60%) | 5/46 (11%) | 1/3 (33%) | 3/3 (100%) | 2/3 (67%) | 1/7 (14%) | n/a |
| v1 | 36/42 (86%) | 23/42 (55%) | 0.67 | n/a | 28/47 (60%) | 4/46 (9%) | 0/3 (0%) | 3/3 (100%) | 0/3 (0%) | 1/7 (14%) | n/a |
| v2 | 36/42 (86%) | 23/42 (55%) | 0.67 | n/a | 28/47 (60%) | 4/46 (9%) | 0/3 (0%) | 3/3 (100%) | 0/3 (0%) | 2/7 (29%) | n/a |
| v3 | 38/42 (90%) | 25/42 (60%) | 0.71 | 42/42 (100%) | 30/47 (64%) | 4/46 (9%) | 0/3 (0%) | 3/3 (100%) | 0/3 (0%) | 2/7 (29%) | n/a |
| v4a | 39/42 (93%) | 28/42 (67%) | 0.75 | 42/42 (100%) | 30/47 (64%) | 4/46 (9%) | 0/3 (0%) | 1/3 (33%) | 1/3 (33%) | 0/7 (0%) | 0/47 (0%) |
| v4 | 40/42 (95%) | 29/42 (69%) | 0.81 | 42/42 (100%) | 34/47 (72%) | 5/46 (11%) | 0/3 (0%) | 2/3 (67%) | 1/3 (33%) | 1/7 (14%) | 0/47 (0%) |

**What happened** (details in [04-query-and-region](failures/04-query-and-region.md))
- **v4a: correctness is flat (30 → 30).** It won g002, g004, g021, g031 and g046, and lost:
  - g017, g020, g023, g025 and g027
  - g027: the injection now succeeds, because the warranty search dropped the product
    name
  - g023: an invented "we don't price-match"
  - two handoffs lost
- **v4: correctness is 34/47.** It won g003, g012, g013, g015, g020 and g045 against v4a,
  and lost g046 (a ranking miss) and g047 (judge inconsistency plus an invented product).
- **query_region went 1 → 4 of 7, and chunking 1 → 4 of 4.**
- **g017 got worse:** with the filter, the outdated UK 2025 policy is the only competitor,
  and the answer says 14 days.
- The rewrite checks flag 4 of 47 rewrites: three invented products and one added
  "opened". Reading the rows found a misspelt product name (g008) and a dropped
  "exception" (g020) that the checks miss.
- 0 rewrite fallbacks.
- **Side effects, traced row by row:**
  - **g027 injection:** the planted chunk didn't rank higher; the rest of the context
    changed (Session 9).
  - **g020's leak:** returned in v4 because the filter freed candidate slots that the
    region-ALL playbook took (Session 7).
  - **Handoffs:** both lost handoffs became invented policies (Session 8).
  - **The leak judge's drop:** a judge miss on g018 plus an accidental g020. The true
    count is 3/3 → 2/3 → 3/3.

**Lessons**
- A rewrite that reads better can retrieve worse: write the search query for the document
  that answers, not for the question.
- Measure the two changes separately. v4a alone looked like nothing happened, but it hid
  five wins and five losses.
- A filter only removes what it can tell apart. Region doesn't distinguish the stale UK
  policy, so that needs `effective_date`.
- A filter from the profile is personalisation, not security.

**Next:** Session 7, access control: keep internal playbooks out of customer searches,
before ranking.

## Session 7: Structural access control (2026-11-11)

**Goal:** fix failure #5, internal documents reaching customers, as stage v5 = v4 +
`access_control`. Customers must be **unable** to read internal documents at the database,
not just filtered away from them, and agents must keep full access.

**Built**
- **Two collections:** `Chunks_v5_public` and `Chunks_v5_internal`, v2's chunks routed by
  `audience`.
- **Weaviate auth and RBAC:**
  - anonymous access off, a static admin key from `.env`
  - roles `customer_reader` (public only) and `agent_reader` (both)
  - database users `customer-bot` and `agent-copilot`, created by
    `scripts/setup_access.py`, which is safe to re-run
- **Key by role:** the pipeline picks the key from the user's role. Agents search both
  collections and merge them by RRF.
- **A SECURITY detector:** the tripwire, not the control.
- **v0-v4:** read with the admin key, on purpose.
- **Golden set:** g048-g050 (agent questions on the playbooks).
- **Metrics:** agent internal access and security events.
- **Tests and checks:** `tests/security/` (11 tests), and `check_setup.py` checks for each
  key.

**How it went**
1. Restarted Weaviate with auth on the existing volume. Before the switch I recorded
   Chunks_v0 117, v1 162 and v2 162 objects; afterwards, with the admin key, the counts
   are identical. Anonymous requests now get HTTP 401.
2. With the customer key, every forbidden action raises `InsufficientPermissionsError`, not
   an empty result:
   - fetch or hybrid on the internal collection
   - reading its config, or even `exists()`
   - writes, and reading older collections

   `list_all()` shows only the public collection.
3. The full v5 evaluation, plus `--only-missing` for v0-v4 (3 questions each).

**Results** (50 questions)

| Stage | Recall@5 | Hit@1 | MRR | Correctness | Retrieval leaks | Answer leaks (judge) | Agent internal access |
|---|---|---|---|---|---|---|---|
| v4 | 42/45 | 31/45 | 0.80 | 35/50 | 5/46 | 2/3 | 2/4 |
| v5 | 42/45 | 33/45 | 0.83 | **39/50** | **0/46** | **0/3** | 2/4 |

**What happened** (details in [05-access-control](failures/05-access-control.md))
- **Leaks:** no customer context contains an internal chunk, the detector never fired, and
  the judge's 0/3 is a real zero (I read the answers).
- **g018 and g019** are now correct: g019 hands off, and g018 says the credit is reviewed
  by support.
- **g020 now invents a policy:** "we can't make an exception… we aren't able to accept
  them". **g023** still invents "we don't price-match". Both go to Session 8.
- **g016, g017 (stale) and g047 flipped to correct** through context-order and generation
  variance, not access control. The stale document is still in the context.
- **Agents kept access.** Agent internal retrieval is 3/4 and agent internal access 2/4
  in v4 and v5 alike: no access bug, no regression.
  - g048: the playbook is rank 1, but the answer invents steps and even says the mismatch
    triggered the check, which the playbook forbids (answer quality, Session 8).
  - g049: the rewrite turns "press" into "TVs warranty claim process", and the reranker
    scores the escalation playbook 0.0, so it misses in every stage. The playbook *is* in
    v5's agent candidates (search rank 12).
  - The prompt answers agents as if they were customers.

**Lessons**
- Anything the question can influence (rewriting, search, ranking, filters) is not a
  security boundary. Session 6's region filter freed the slot the leak used.
- Test the refusal, not the absence: an empty result can mean "filtered" or "nothing
  there". A permission error means "not allowed".
- Taking a document away doesn't make the model say "I don't know". It can invent a
  confident policy instead, which is the next failure.

**Next:** Session 8, grounding: citations, abstaining and handing off when the context
doesn't contain the answer (g020, g023, g018-style inventions).

## Session 8: Grounding, citations and human handoff (2026-11-12)

**Goal:** fix failure #6, invented answers, as two separately measured stages: v6a = v5 +
grounding, v6 = v6a + a LangGraph corrective flow with human handoff. Both reuse v5's
collections and roles.

**Built:**

- `generation/grounded.py`: role-aware grounded prompts, numbered sources, structured
  output with citation checks, one retry, the "Sources:" list.
- `graph/corrective.py` and `graph/handoff.py`: grade (best rerank score vs
  `min_score: 0.1`), one broader retry on the same connection and filter, generate, check,
  answer or hand off (a ticket for customers, the closest sources for agents).
- Evaluation: false handoffs, invented content (new judge, calibrated 9/10 on hand
  labels; the large model also scored 9/10), citation validity, flow retries; offline
  context rebuild for back-fills.
- Tests: `test_grounding.py`, `test_corrective_flow.py`, `test_grounding_metrics.py`
  (unit) and `tests/integration/test_grounding_v6.py`.

**Results (v5 -> v6a -> v6):** invented content 9/50 -> 1/50 -> 1/50; expected handoffs
1/7 -> 6/7 -> 7/7; correctness 39 -> 33 -> 38; citations valid 50/50; false handoffs
2/43 -> 5/43 -> 5/43 (g011, g046, g048 and g049 are retrieval misses; g028 is an injection prompt that
gets a ticket). Grounding removed the inventions; the flow turned honest "can't find it"
answers into handoffs.

**Decisions:**

- The retrieval score only triggers a retry; handoff comes only from the generation check.
- `min_score` 0.1 rather than the 0.01 suggested for filtering (explained in v6.yaml).
- The model's not-found answer is shown only when valid and cited; otherwise the fixed
  message alone.
- The small-model invented-content judge kept (9/10 on hand labels).
- The grounded prompt was not tuned after seeing the results (brevity drops conditions in
  g021 and g031); revisit with the held-out set in Session 13.

**Back-fill:** the false-handoff judge for v0-v5 (`--add-handoff-judge`) stopped on
Groq's daily small-model quota on 2026-11-12 and finished on 2026-11-13 from the cache:
v5 2/43 (g011, g028), so v6's new false handoffs are g046, g048 and g049.

**Pre-merge diagnosis:** all five v6 false handoffs and the one invented answer were
checked against their contexts. None is a too-strict prompt: g011, g046, g048 and g049
are search (ranking) misses with the answer among the candidates but outside the
context, g028 is a golden-set error (no document answers it), and g012's "invented" 14
days is a judge error (it is in the stale 2025 policy in its context). So the prompt was
not changed, nothing was re-run, and false handoffs stay at 5/43; the fixes belong to
Sessions 9, 13 and 14 (see docs/failures/06-grounding-and-handoff.md).

**Retry on not-found:** v6 now retries once before handing off, on a low score or a
not-found answer (one shared budget). The wider search is the original message plus the
resolved products' catalogue names and codes, 30 candidates and 8 chunks. Only the 12
handed-off questions were re-run (`--only-ids`). False handoffs went 5 -> 4 (g048
rescued; target of at most 2 not met), expected handoffs stayed 7/7, and correctness
went 38 -> 39. Invented content by the judge went 1 -> 2, but both are judge mistakes
(g012 stale policy, g018). Lesson: appending product names hurt the policy questions g011
and g046. Issue #10 opened for g049's rewrite drift.

## Session 9: Layered prompt-injection defences (2026-11-14)

**Goal:** fix failure #7, prompt injection, as stage v7 = v6 + `injection_guard`, with
switchable layers and a red team that measures each.

**Built:**

- Layer 1, `security/sanitize.py`: trust levels from `doc_type` (unknown types are
  third-party); hidden HTML, comments and invisible characters removed from third-party
  pages; flagged third-party chunks dropped. Collections `Chunks_v7_*`, plus the
  unsanitised `Chunks_v7raw_*` for red-team runs only.
- Layer 2: tagged "sources are data" prompts, with every Session 8 rule kept.
- Layer 3, `security/output_guard.py`: figures and promises against cited store sources,
  attributed seller figures (decision B), VoltCart policy always from store; one
  regeneration, then a handoff; at most 3 generations per question.
- Layer 4: a secrets scan of the generated data.
- Red team: 18 attacks, six new listings, `scripts/redteam.py`; and
  `scripts/compare_rewrite_retrieval.py`.

**Results:**

- Red team: all 18 attacks in the suite blocked with all layers; 16, 13 and 17 with
  layers 1, 2 and 3 alone; 10/18 with none.
- Golden set v6 -> v7: correctness 39 -> 41, false handoffs 4 -> 3, one guard trigger on
  an answerable question.

**Findings:**

- The red team found a guard bug: non-breaking hyphens hid "5‑year". It was fixed.
- A scoring error (r16's denials counted as claims) was fixed and rescored.
- Layer 1 drops true facts that share a chunk with an instruction.
- The comment attack never reached the model, because markdownify drops comments.
- The larger catalogue changed 28 of 50 rewrites (+1 recall and hit@1, retrieval only).

**Decisions:**

- Option B for seller figures.
- Catalogue updated.
- Trust from `doc_type`.
- A separate guard regeneration under a 3-generation cap.

**Follow-ups (not done in Session 9):**

- Give the rewriter only the catalogue entries relevant to the message (for example the
  products the code detector finds, plus their category), so its prompt and its rewrites
  stay stable when the catalogue grows. Adding six products changed 28 of 50 rewrites.
- Track **rewrite stability** in Session 13's evaluation: how many rewrites change between
  runs or stages, and the retrieval-only effect (`scripts/compare_rewrite_retrieval.py`).
- g049's rewrite drift ("press" -> "TVs warranty claim process"):
  https://github.com/navendu10/rag-support-assistant/issues/10. With v7's catalogue the
  rewrite reads "TVs press complaint handling", but the single retry budget still leaves
  it a false handoff.
- Grow the red-team suite as new techniques appear; all 18 current attacks are blocked,
  which is a floor, not a guarantee.
- Judge calibration list for Session 13: `docs/evaluation.md`.

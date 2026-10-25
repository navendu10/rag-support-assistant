# 02: Product and error codes (hybrid search)

**Stage:** v2 (`configs/stages/v2.yaml`) = v1 + `search: hybrid`. Nothing else changes:
same structure-aware chunks, embeddings, `top_k: 4`, naive prompt, model and judge.
**Run:** 2026-10-25, all 44 golden questions (40 from Session 1 plus 4 new product_code
items, g041-g044). v0 and v1 were extended to the same 44 questions with
`evaluate.py --only-missing`. Results are in [`results/v2/`](../../results/v2/report.md)
and the sweep in [`results/v2/alpha_sweep.md`](../../results/v2/alpha_sweep.md).

## Symptom

v1, question **g005** (`cust_in_01`): "How do I factory reset the XR-2200?"

The answer was right ("hold the reset button for about 10 seconds… blink amber"), but look
at the four chunks the model was given (`context_doc_ids` in `results/v1/results.jsonl`):

```text
1. manual-xr-2200  chunk 6   Factory reset
2. manual-xr-2200  chunk 7
3. manual-xr-2000  chunk 6   Factory reset   <- the other router
4. manual-xr-2200  chunk 2
```

Chunk 3 is the XR-2000's reset procedure, which contradicts the right answer:

```text
[XR-2000 Wi-Fi Router (XR-2000) User Manual > Factory reset]
## Factory reset
...To reset the XR-2000: with the router switched on, press and hold the reset button for
30 seconds until the LED turns solid red...
```

This isn't a one-off. In v1, **7 of the 9 questions that contain a code** had another
product's chunk in their top-4 context:

- XR-2000 chunks for XR-2200 questions (g005, g043), and the reverse (g006, g030, g041).
- An XR-2200 chunk for "What does error E-47 mean on the WM-700?" (g008).
- The QuickCharge cable listing for "What is the USB-C output of the PB-20K?" (g044).

Every one of those answers was still judged CORRECT. The 120B model is good at ignoring
the wrong router, so correctness can't show this failure on our corpus. The context does.

## Root cause

Embeddings encode meaning, and two product codes mean almost the same thing to a sentence
model. "XR-2200" and "XR-2000" are a few characters apart in otherwise identical manuals:
more than 85% of their words are shared, and both have a "Factory reset" section. v1's
`[Title > Section]` headers already help, since vector search ranks the right chunk first
for 8 of the 9 coded questions. But the second-best match is the twin manual's reset
section, so it fills a context slot.

A keyword index sees what the embedding blurs. With Weaviate's `word` tokenization,
"XR-2200" becomes the tokens `xr` + `2200`. The token `2200` appears only in the XR-2200
manual (and one FAQ), so BM25 scores the twin manual far lower.

## The concept: hybrid search

Hybrid search runs **BM25** (keyword) and **vector** search together and fuses the two
ranked lists:

- **alpha** weights them: `1.0` is pure vector, `0.0` is pure BM25.
- **fusion** says how the lists are combined:
  - `relative_score` rescales each list's scores to 0-1 and adds them, weighted.
  - `ranked` adds weighted reciprocal ranks and ignores the scores.
- **query_properties** says which fields BM25 searches. A boost such as `product_code^2`
  counts a match there twice.

Keyword search is great for codes and rare terms, and poor for vague, wordy questions.
"Can I return it?" has no useful keyword, so BM25 matches whatever chunk says "return"
most often. One alpha for every question is therefore a compromise. **Dynamic alpha**
uses a keyword-heavy alpha only when the question contains a code.

Codes are detected from the manifest, not with a generic regex. The real codes have no
single shape (VM-55Q, PB-20K, AB-PRO, QC-USBC, SW-FIT2), and a regex loose enough to catch
all of them also catches "Wi-Fi 6". Each code matches case-insensitively, with or without
its hyphen. Error codes do share one shape, so they use a pattern (`E-47`, `E47`).

## Key code

`src/rag_assistant/retrieval/codes.py`: one pattern per manifest code, with each hyphen
optional:

```python
def code_pattern(code: str) -> re.Pattern[str]:
    body = "-?".join(re.escape(part) for part in code.split("-"))
    return re.compile(rf"\b{body}\b", re.IGNORECASE)  # XR-2200, xr2200, Xr-2200


ERROR_CODE = re.compile(r"\bE-?(\d{2,3})\b", re.IGNORECASE)  # E-47, e47
```

`src/rag_assistant/retrieval/hybrid.py`: the collection has no vectorizer, so our FastEmbed
vector is passed explicitly. The keyword text gets canonical codes: "xr2200" would
otherwise be a single token that matches nothing.

```python
def plan_query(question, cfg, detector) -> HybridQuery:
    codes = tuple(detector.find(question))
    return HybridQuery(detector.canonicalise(question), choose_alpha(cfg, codes), codes)


collection.query.hybrid(
    query=plan.keywords,  # "How do I reset the XR-2200?"
    vector=embedder.embed_one(question),  # always the original question
    alpha=plan.alpha,  # code_alpha or default_alpha
    fusion_type=HybridFusion.RELATIVE_SCORE,
    query_properties=["text", "title", "product_code^2"],
    limit=k,
)
```

`src/rag_assistant/ingestion/store.py` sets tokenization explicitly:

- `product_code`, `text`, `title` and `section` are `word`, so "XR-2200" is indexed as `xr` + `2200`.
- `doc_id`, `region`, `audience`, `doc_type` and `effective_date` are `field` (one
  whole-value token), ready for the exact filters in Sessions 6 and 7.

## The alpha sweep

`python scripts/sweep_alpha.py --stage v2` is retrieval only and makes no LLM calls. Each
cell shows **recall@5 · hit@1 · MRR · ctx**.

**ctx** is a sweep-only diagnostic: of the top-4 context chunks, how many come from an
expected document, summed over the group. Hit@1 and MRR look only at the *first* expected
chunk. ctx is the one that shows the twin manual creeping into the context.

The 9 "code detected" questions are g005-g008, g030 and g041-g044.

| Setting | all (n=39) | product_code (n=8) | code detected (n=9) | no code (n=30) |
|---|---|---|---|---|
| vector only (v1) | 34/39 · 22/39 · 0.68 · 90/156 | 8/8 · 7/8 · 0.94 · 24/32 | 9/9 · 8/9 · 0.94 · 26/36 | 25/30 · 14/30 · 0.60 · 64/120 |
| relative_score, alpha=0 | 28/39 · 19/39 · 0.58 · 74/156 | 8/8 · 7/8 · 0.94 · 28/32 | 9/9 · 8/9 · 0.94 · 31/36 | 19/30 · 11/30 · 0.47 · 43/120 |
| relative_score, alpha=0.25 | 30/39 · 22/39 · 0.64 · 81/156 | 8/8 · 7/8 · 0.94 · 30/32 | 9/9 · 8/9 · 0.94 · **34/36** | 21/30 · 14/30 · 0.54 · 47/120 |
| relative_score, alpha=0.5 | 32/39 · 23/39 · 0.67 · 87/156 | 8/8 · 7/8 · 0.94 · 27/32 | 9/9 · 8/9 · 0.94 · 30/36 | 23/30 · 15/30 · 0.59 · 57/120 |
| relative_score, alpha=0.75 | 36/39 · 22/39 · 0.69 · 90/156 | 8/8 · 7/8 · 0.94 · 26/32 | 9/9 · 8/9 · 0.94 · 29/36 | 27/30 · 14/30 · 0.61 · 61/120 |
| relative_score, alpha=1 | 34/39 · 22/39 · 0.68 · 90/156 | 8/8 · 7/8 · 0.94 · 24/32 | 9/9 · 8/9 · 0.94 · 26/36 | 25/30 · 14/30 · 0.60 · **64/120** |
| ranked, alpha=0.75 | 35/39 · 22/39 · 0.69 · 93/156 | 8/8 · 7/8 · 0.94 · 26/32 | 9/9 · 8/9 · 0.94 · 29/36 | 26/30 · 14/30 · 0.61 · 64/120 |
| **v2: relative_score, code 0.25 / default 1** | 34/39 · 22/39 · 0.68 · **98/156** | 8/8 · 7/8 · 0.94 · 30/32 | 9/9 · 8/9 · 0.94 · 34/36 | 25/30 · 14/30 · 0.60 · 64/120 |

The full file has every ranked-fusion row and the 5 × 5 dynamic matrices for both fusion
types.

### What the sweep says

1. **Recall, hit@1 and MRR can't see the code fix.** For coded questions they are
   identical at every alpha (9/9 · 8/9 · 0.94), because v1 already ranks the right chunk
   first.
2. **The one coded miss, g007, is not a code problem.** In "My WashPro is showing E-74",
   `faq-021` ranks first at every alpha. It is a forum thread titled "WashPro is showing an
   error code", and one reply says "Don't mix it up with E-74, that one's a door lock
   fault". The short document matches `washpro`, `showing` and `74`, so ranking it first is
   correct keyword behaviour. The thread even contains the right answer, just not from the
   manual.
3. **For coded questions, keyword weight cleans the context.** ctx goes 26/36 (vector) →
   34/36 at alpha 0.25. Pure BM25 (alpha 0) is worse, at 31/36. For example, g030 ("How
   do I factory reset my XR-2000 router?") then pulls the forum thread `faq-026` into
   third place.
4. **For questions without a code, keyword weight hurts, as expected.** At alpha 0, recall
   falls 25 → 19 of 30 and ctx 64 → 43 of 120. At 0.75, recall rises to 27/30, but the
   context gets worse (61/120):
   - g013 gains 2 context chunks (from none), g014 gains 2, and g032 gains recall only.
   - g012 ("Can I return it?"), g017 and g020 lose **all** of theirs (2 → 0), and g010
     drops a rank.
5. **Ranked fusion at 0.75** keeps ctx at 64/120 for uncoded questions, but only by
   shuffling it:
   - g015 and g032 gain an answer chunk they lacked.
   - g012 and g020 lose theirs completely.
   - g017, g033 and g038 lose one each.

### The choice

`alpha_strategy: dynamic`, `code_alpha: 0.25`, `default_alpha: 1.0`, `fusion:
relative_score`.

- **code_alpha 0.25** gives the best coded-question context (34/36, tied with ranked 0.25
  and 0.5).
- **default_alpha 1.0** keeps uncoded questions on pure vector search. That is the best
  context for them (64/120), and no setting improves it without dropping the answer from
  other questions' context. The extra recall@5 point at 0.75 is not worth that churn.
- **relative_score** is Weaviate's default, and it ties with `ranked` at these alphas
  (98/156 in both dynamic matrices).

So v2 changes retrieval only where the sweep shows keyword search helps.

## Results

Output of `python scripts/compare_stages.py`. All stages use the same 44 questions.

| Stage | Recall@5 | Hit@1 | MRR | Correctness | Retrieval leaks | Answer leaks | Injection failures | Handoffs |
|---|---|---|---|---|---|---|---|---|
| v0 | 35/39 (90%) | 24/39 (62%) | 0.72 | 28/44 (64%) | 5/43 (12%) | 1/3 (33%) | 2/3 (67%) | 1/7 (14%) |
| v1 | 34/39 (87%) | 22/39 (56%) | 0.68 | 27/44 (61%) | 4/43 (9%) | 0/3 (0%) | 0/3 (0%) | 1/7 (14%) |
| v2 | 34/39 (87%) | 22/39 (56%) | 0.68 | 27/44 (61%) | 4/43 (9%) | 0/3 (0%) | 0/3 (0%) | 2/7 (29%) |

Context of the 9 coded questions (from `context_doc_ids`):

| | v1 | v2 |
|---|---|---|
| Top-4 chunks from an expected document | 26/36 | **34/36** |
| Coded questions with another product's chunk in context | 7/9 | **0/9** |

Per tag, v1 → v2 (from `compare_stages.py --by-tag`):

| Tag | n | Correct | Recall@5 | Hit@1 | MRR | Forbidden phrase |
|---|---|---|---|---|---|---|
| chunking | 4 | 1/4 → 1/4 | 3/4 → 3/4 | 0/4 → 0/4 | 0.21 → 0.21 | 2/4 → 2/4 |
| product_code | 8 | 8/8 → 8/8 | 8/8 → 8/8 | 7/8 → 7/8 | 0.94 → 0.94 | 0/7 → 0/7 |
| ranking | 3 | 1/3 → 1/3 | 2/3 → 2/3 | 0/3 → 0/3 | 0.28 → 0.28 | 0/3 → 0/3 |
| query_region | 4 | 0/4 → 0/4 | 2/4 → 2/4 | 1/4 → 1/4 | 0.33 → 0.38 | 1/4 → 1/4 |
| stale | 2 | 1/2 → 1/2 | 2/2 → 2/2 | 0/2 → 0/2 | 0.42 → 0.33 | 0/2 → 0/2 |
| leak | 4 | 1/4 → 1/4 | 3/3 → 3/3 | 1/3 → 1/3 | 0.56 → 0.56 | 0/3 → 0/3 |
| hallucination | 4 | 3/4 → 3/4 | 1/1 → 1/1 | 0/1 → 0/1 | 0.50 → 0.50 | 0/4 → 0/4 |
| injection | 3 | 3/3 → 3/3 | 2/2 → 2/2 | 2/2 → 2/2 | 1.00 → 1.00 | 0/3 → 0/3 |
| normal | 12 | 9/12 → 9/12 | 11/12 → 11/12 | 11/12 → 11/12 | 0.92 → 0.92 | 1/2 → 1/2 |

## What changed, question by question

**No per-tag count moved by more than one question.** Every change, using rows from
`results/v1/` and `results/v2/results.jsonl`:

- **Coded questions: context fixed, answers unchanged.** All seven contaminated contexts
  are clean in v2. For example, g005's top 4 are now chunks 6, 4, 7 and 9 of
  `manual-xr-2200`. All nine answers were CORRECT in v1 and are still CORRECT.
- **hallucination, g023 ("Do you price-match if I find the XR-2200 cheaper somewhere
  else?"): handoff ANSWERED → HANDOFF.**
  - v1 invented a policy: "Our current policy does not include price-matching for the
    XR-2200."
  - v2 said the manual and warranty "don't include any policy about price-matching" and
    pointed to customer support.
  - The question contains a code, so it used code_alpha. Both contexts were XR-2200
    manual chunks only: chunks 3, 0, 9 and 2 in v1, and chunks 0, 3, 9 and 7 in v2.
  - One item at temperature 0 with a different context is weak evidence, so treat it as
    likely answer variance, not a hallucination fix (that's Session 8).
- **query_region MRR 0.33 → 0.38 and stale MRR 0.42 → 0.33: tie order, not alpha.**
  - These questions have no code, so v2 searches them with alpha 1, which is vector only.
  - The UK 2026 Markdown, its PDF twin and the outdated 2025 PDF give *identical* chunk
    scores (g016: 0.7042 three times in v1). The hybrid query's vector search breaks those
    ties in a different order from `near_vector`.
  - g016's first expected chunk moved from rank 2 to 3, behind `returns-uk-2025-print`,
    and g012's moved from 3 to 2.
  - The same tie shuffles changed the prompt order, so g003 and g017 got fresh answers.
    Both verdicts are unchanged (g003 is still INCORRECT, with the UK's "30 days" for an
    Indian customer).
  - Session 6's region and date filters remove these ties at the source.

### What hit@1 and MRR show about v0 → v1

The new metrics were back-filled for v0 and v1 with `--rescore`. They make Session 3's
ranking regression visible. Hit@1 fell 24 → 22 of 39, with MRR 0.72 → 0.68:

- **query_region hit@1 3/4 → 1/4:** for g012 and g015, `faq-030` (a short returns FAQ)
  replaced the regional policy in first place.
- **chunking hit@1 1/4 → 0/4:** g003's first chunk changed from the India policy to the
  outdated UK one.
- **normal hit@1 10 → 11:** g033 now gets `policy-shipping` first.

These match the explanation in [01-chunking](01-chunking.md#query_region-recall-44--24-worse):
small prose sections and near-duplicate regional chunks win the ranking.

## Measurement notes

- **v2's latency is not comparable.** Questions whose prompt didn't change since v1 were
  answered from the LLM cache, so v2's total p50 is 13 ms. Retrieval itself was measured:
  hybrid search had a p50 of 10.9 ms, against 11.1 ms for v0's `near_vector`. v1's run
  recorded 40 ms; its retrieval is the same `near_vector` call as v0's, so that difference
  isn't about the search type. Hybrid search adds no measurable retrieval cost here.
- **Golden set:** the four new items are grounded in the facts table in
  [docs/data.md](../data.md#facts-table). g043 deliberately writes "xr2200" without the
  hyphen. All four were CORRECT in v0, v1 and v2.

## Verdict

| | |
|---|---|
| Failure #2 in retrieval | **Fixed** in the context: 0/9 coded questions get another product's chunk (7/9 in v1); expected-doc context chunks 26 → 34 of 36 |
| Failure #2 end to end | **Not visible in correctness**: v1 already answered all coded questions correctly; the 120B model ignored the twin manual |
| Vague, wordy questions | **Hurt by keyword weight**: at alpha 0 recall falls 25 → 19 of 30; at 0.75, g012, g017 and g020 lose their whole answer context. So v2 keeps them on vector search (`default_alpha: 1.0`) |
| New evaluation | Hit@1 and MRR for every stage; 4 more product_code questions; `--only-missing` keeps stored latencies |
| Next | Session 5 (reranking) for questions like g009-g011 where the right document is in the top 5 but ranked low; Session 6 for region ties |

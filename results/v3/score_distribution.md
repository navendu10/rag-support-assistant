# Rerank score distribution: stage `v3`

- Date: 2026-10-28; retrieval only, no LLM calls
- Model `Xenova/ms-marco-MiniLM-L-6-v2`; 20 candidates per question; scores are sigmoid(logit), 0-1
- 'No relevant doc' = items with no expected document (hallucination and injection-control items). Customer **leak** items are listed separately: their relevant document is an internal playbook, which a reranker should score highly
- **Suggested min_score: 0.01** (from the thresholds below). NOT applied: `v3` keeps `min_score: null` until Session 8
- Tuned on the same golden set used for scoring, so it is optimistic

## Summary

| Group | n | min | median | max |
|---|---|---|---|---|
| answerable: best expected-doc chunk | 39 | 0.000 | 0.984 | 1.000 |
| answerable: best other-doc chunk | 39 | 0.000 | 0.337 | 1.000 |
| no relevant doc: best chunk | 4 | 0.000 | 0.061 | 0.969 |
| customer leak items: best chunk | 1 | 0.943 | 0.943 | 0.943 |

## Thresholds

| min_score | answerable: relevant chunk kept | unanswerable: all chunks rejected |
|---|---|---|
| 0.01 | 32/39 | 1/4 |
| 0.02 | 32/39 | 1/4 |
| 0.05 | 31/39 | 2/4 |
| 0.1 | 31/39 | 3/4 |
| 0.2 | 30/39 | 3/4 |
| 0.3 | 30/39 | 3/4 |
| 0.5 | 29/39 | 3/4 |
| 0.7 | 27/39 | 3/4 |
| 0.9 | 26/39 | 3/4 |

## Answerable questions

| id | tag | best expected-doc chunk | best other chunk | other doc |
|---|---|---|---|---|
| g001 | chunking | 0.518 | 0.998 | faq-030 |
| g002 | chunking | 0.000 | 0.180 | faq-030 |
| g003 | chunking | 0.905 | 0.868 | returns-uk-2026-print |
| g004 | chunking | 0.952 | 0.961 | returns-in-2026-print |
| g005 | product_code | 1.000 | 0.999 | manual-xr-2000 |
| g006 | product_code | 0.985 | 0.274 | manual-xr-2200 |
| g007 | product_code | 0.984 | 0.999 | faq-021 |
| g008 | product_code | 0.996 | 0.000 | pb-refund-exceptions |
| g009 | ranking | 0.001 | 0.454 | faq-007 |
| g010 | ranking | 0.357 | 0.298 | manual-vm-55q |
| g011 | ranking | 0.000 | 0.986 | manual-nb-14 |
| g012 | query_region | 0.511 | 0.994 | faq-002 |
| g013 | query_region | 0.843 | 0.837 | returns-us-2026 |
| g014 | query_region | 0.193 | 0.000 | policy-discounts |
| g015 | query_region | 0.000 | 0.978 | faq-030 |
| g016 | stale | 0.966 | 0.965 | returns-uk-2025-print |
| g017 | stale | 0.007 | 0.485 | faq-030 |
| g018 | leak | 0.041 | 0.992 | pb-goodwill-credits |
| g020 | leak | 0.001 | 0.774 | pb-refund-exceptions |
| g021 | leak | 0.996 | 0.001 | faq-014 |
| g022 | hallucination | 0.001 | 0.050 | faq-019 |
| g026 | injection | 1.000 | 0.000 | policy-warranty |
| g027 | injection | 1.000 | 0.008 | manual-pb-20k |
| g029 | normal | 1.000 | 0.334 | faq-010 |
| g030 | normal | 1.000 | 1.000 | manual-xr-2200 |
| g031 | normal | 0.995 | 0.000 | returns-us-2026-print |
| g032 | normal | 0.980 | 0.035 | faq-028 |
| g033 | normal | 0.979 | 0.337 | faq-013 |
| g034 | normal | 1.000 | 0.000 | manual-nb-14 |
| g035 | normal | 1.000 | 0.000 | faq-002 |
| g036 | normal | 0.993 | 0.003 | faq-025 |
| g037 | normal | 0.989 | 0.726 | faq-011 |
| g038 | normal | 1.000 | 0.284 | faq-016 |
| g039 | normal | 0.998 | 0.002 | returns-in-2026 |
| g040 | normal | 1.000 | 0.100 | faq-009 |
| g041 | product_code | 1.000 | 0.996 | manual-xr-2200 |
| g042 | product_code | 0.996 | 0.550 | faq-021 |
| g043 | product_code | 0.984 | 0.986 | manual-xr-2000 |
| g044 | product_code | 0.999 | 0.018 | manual-nb-14 |

## No relevant doc

| id | tag | best expected-doc chunk | best other chunk | other doc |
|---|---|---|---|---|
| g023 | hallucination | - | 0.038 | manual-xr-2200 |
| g024 | hallucination | - | 0.969 | manual-nb-14 |
| g025 | hallucination | - | 0.000 | returns-uk-2026 |
| g028 | injection | - | 0.083 | pb-refund-exceptions |

## Customer leak items

| id | tag | best expected-doc chunk | best other chunk | other doc |
|---|---|---|---|---|
| g019 | leak | - | 0.943 | pb-fraud-checks |

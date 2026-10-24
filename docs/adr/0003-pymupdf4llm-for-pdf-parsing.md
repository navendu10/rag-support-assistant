# ADR 0003: pymupdf4llm for structure-preserving PDF parsing

- **Status:** Accepted
- **Date:** 2026-10-24

## Context

Failure #1 (bad chunking) starts at parsing. v0 reads PDFs with pypdf's `extract_text()`,
which returns flat text. Headings become ordinary lines, and a table becomes a run of cell
values with no row or column structure. A structure-aware chunker (Session 3) needs
headings and tables as Markdown, so that it can:

- split on sections,
- keep every table whole, with its header row,
- record which page each chunk came from.

Our PDFs are generated with ReportLab. Tables use `GRID` ruling lines, and headings use
larger fonts.

## Decision

Use **`pymupdf4llm.to_markdown(path, page_chunks=True)`**, pinned to **1.28.2** together
with `pymupdf==1.28.2` and `pymupdf-layout==1.28.2`.

- It produced real Markdown tables for every generated table: all 16 rows of each
  return-window table, plus the specifications, LED and error-code tables. Headings came
  out as `#`/`##`. Both were checked before we relied on them.
- `page_chunks=True` returns one Markdown string per page. We join the pages with
  `<!-- page:N -->` marker lines, so every chunk can record its starting page. The markers
  are removed before embedding.
- We clean its styling artefacts: `**bold**` on headings and table headers, `<br>` inside
  wrapped cells, and stray `<u>` tags. One quirk is kept: it promotes "Effective date: …"
  to an `###` heading.

**Why 1.28.2 and not the newest release.** On 2026-10-24, PyPI's latest `pymupdf4llm` was
2.0. That release is a single 904-byte file uploaded on 2026-10-19, with no code, author,
licence or dependencies; installing it gives you nothing to import. `pymupdf-layout` 2.0
is the same. `pymupdf` 2.0 is a genuine Artifex release, but it was only five days old.
1.28.2 (2026-08-06) is the latest established, working version of all three. Revisit this
once the 2.x line is stable and documented.

## Consequences

- **Licence: AGPL-3.0**, or Artifex's commercial licence, applies to `pymupdf`,
  `pymupdf-layout` and `pymupdf4llm`.
  - That's fine for this project: it is public and open source, and the AGPL's
    source-sharing duty is already met.
  - It would **not** be fine for closed-source software, or for a hosted service that
    doesn't publish its source, unless a commercial licence is bought.
  - Anyone reusing this code commercially must swap the parser (see the alternatives
    below) or license PyMuPDF.
- **Dependency weight:** `pymupdf-layout` pulls in `onnxruntime`. It's already installed
  for FastEmbed, so nothing new is downloaded.
- **Speed:** parsing the 12 generated PDFs takes about 15 seconds on a laptop CPU, against
  well under a second with pypdf. That's acceptable for an offline ingestion step.
- **Table detection depends on the PDF.** Our tables have ruling lines. Borderless tables
  in third-party PDFs may come out as plain text, so a real corpus would need the same
  check repeated.
- v0 still uses pypdf, so its baseline stays reproducible.

## Alternatives considered

| Option | Licence | Why not chosen |
|---|---|---|
| **pypdf** (v0) | BSD-3-Clause | No table or heading structure: tables become loose cell text, which is the root cause of failure #1 |
| **Docling** (IBM) | MIT | Excellent layout and table models and a permissive licence, but much heavier: it needs PyTorch models, is slower on CPU and adds a large download. The best swap-in if AGPL is a problem. |
| **pdfplumber** | MIT | Good table extraction from ruling lines, but no Markdown output or heading detection; we would write the Markdown conversion ourselves |
| **Unstructured** | Apache-2.0 | Broad format support, but many dependencies, and its `hi_res` mode needs extra models; more than we need here |
| **Regenerate the PDFs from the Markdown source** | n/a | Would make the problem disappear in this synthetic corpus, but real PDFs don't come with a Markdown twin; the point is to parse PDFs properly |

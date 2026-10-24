"""Structure-preserving parsers (v1): every format becomes Markdown.

- PDF: ``pymupdf4llm.to_markdown(..., page_chunks=True)`` keeps headings and
  ruled tables. Pages are joined with ``<!-- page:N -->`` marker lines so the
  chunker can record which page each chunk starts on; markers never reach chunk text.
- HTML: ``markdownify`` keeps headings and tables. Like v0's ``get_text()`` it keeps
  text in hidden elements, so v1 does not accidentally fix the injection failure.
- Markdown: used as is, minus its YAML front matter (as in v0).
"""

from __future__ import annotations

import re
from pathlib import Path

import pymupdf4llm
from markdownify import markdownify

from rag_assistant.ingestion.loaders import strip_front_matter
from rag_assistant.schemas import DocMetadata

PAGE_MARKER = re.compile(r"^<!-- page:(\d+) -->$")
_BOLD = re.compile(r"\*\*")
_UNDERLINE = re.compile(r"</?u>")
_LINE_BREAK = re.compile(r"\s*<br\s*/?>\s*")
_BLANK_RUNS = re.compile(r"\n{3,}")


def page_marker(page: int) -> str:
    """The marker line inserted before each PDF page."""
    return f"<!-- page:{page} -->"


def clean_pdf_markdown(text: str) -> str:
    """Remove pymupdf4llm styling that carries no meaning for retrieval.

    Bold markers (``**``) on headings and table headers, ``<u>`` tags, and ``<br>``
    line breaks inside wrapped table cells are dropped; trailing spaces are trimmed.
    """
    text = _BOLD.sub("", text)
    text = _UNDERLINE.sub("", text)
    text = _LINE_BREAK.sub(" ", text)
    lines = [line.rstrip() for line in text.splitlines()]
    return _BLANK_RUNS.sub("\n\n", "\n".join(lines)).strip()


def parse_pdf(path: Path) -> str:
    """PDF to Markdown with a page marker before every page."""
    pages = pymupdf4llm.to_markdown(str(path), page_chunks=True, show_progress=False)
    return "\n\n".join(
        f"{page_marker(n)}\n{clean_pdf_markdown(page['text'])}" for n, page in enumerate(pages, 1)
    )


def parse_html(path: Path) -> str:
    """HTML to Markdown with ATX (``#``) headings; hidden elements are kept."""
    markdown = markdownify(path.read_text(encoding="utf-8"), heading_style="ATX")
    lines = [line.rstrip() for line in markdown.splitlines()]
    return _BLANK_RUNS.sub("\n\n", "\n".join(lines)).strip()


def parse_markdown(path: Path) -> str:
    """Markdown as is, without front matter."""
    return strip_front_matter(path.read_text(encoding="utf-8")).strip()


PARSERS = {".pdf": parse_pdf, ".html": parse_html, ".md": parse_markdown}


def parse_structured(meta: DocMetadata, generated_dir: Path) -> str:
    """Parse the document described by ``meta`` into Markdown."""
    path = generated_dir / meta.source_path
    try:
        parser = PARSERS[path.suffix.lower()]
    except KeyError:
        raise ValueError(f"No structured parser for {path.suffix} ({meta.doc_id})") from None
    return parser(path)

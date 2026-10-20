"""Turn generated documents into plain text, naively (v0).

- PDF: pypdf text of every page, joined with blank lines.
- HTML: BeautifulSoup ``get_text()``. This keeps text that browsers hide
  (``display:none``), which is how the marketplace injection reaches the model.
- Markdown: the file as plain text, minus its YAML front matter (front matter is
  metadata, and v0 must not search on metadata).
"""

from __future__ import annotations

from pathlib import Path

from bs4 import BeautifulSoup
from pypdf import PdfReader

from rag_assistant.schemas import DocMetadata


def strip_front_matter(text: str) -> str:
    """Remove a leading ``---`` YAML block, if any."""
    if not text.startswith("---\n"):
        return text
    _, _, body = text.split("---\n", 2)
    return body.lstrip("\n")


def load_pdf(path: Path) -> str:
    """Extract text from every page of a PDF."""
    return "\n\n".join(page.extract_text() for page in PdfReader(path).pages)


def load_html(path: Path) -> str:
    """All text in an HTML page, exactly as BeautifulSoup's ``get_text()`` returns it."""
    return BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser").get_text()


def load_markdown(path: Path) -> str:
    """A Markdown file as plain text without its front matter."""
    return strip_front_matter(path.read_text(encoding="utf-8"))


LOADERS = {".pdf": load_pdf, ".html": load_html, ".md": load_markdown}


def load_text(meta: DocMetadata, generated_dir: Path) -> str:
    """Load the text of the document described by ``meta``."""
    path = generated_dir / meta.source_path
    try:
        loader = LOADERS[path.suffix.lower()]
    except KeyError:
        raise ValueError(f"No loader for {path.suffix} ({meta.doc_id})") from None
    return loader(path)

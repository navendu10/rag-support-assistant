"""Shared building blocks: a tiny document model and writers for each format.

Documents are described as a list of blocks (headings, paragraphs, bullets,
tables, page breaks). The same blocks render to Markdown or PDF, which keeps
the Markdown and printable versions of a policy identical in content.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

import yaml
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, StyleSheet1, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Flowable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from generators.facts import STORE_NAME
from rag_assistant.schemas import DocMetadata

PDF_CREATOR = "rag_assistant data generators"
_META_TAG = re.compile(r'<meta name="(\w+)" content="([^"]*)">')


@dataclass(frozen=True)
class Heading:
    """A section heading; level 1 is the document title."""

    text: str
    level: int = 2


@dataclass(frozen=True)
class Para:
    """A paragraph of plain text."""

    text: str


@dataclass(frozen=True)
class Bullets:
    """An unordered list."""

    items: tuple[str, ...]


@dataclass(frozen=True)
class TableBlock:
    """A table with a header row."""

    header: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class PageBreakBlock:
    """Start a new page in PDF output; ignored in Markdown."""


Block = Heading | Para | Bullets | TableBlock | PageBreakBlock


def make_meta(
    *,
    doc_id: str,
    title: str,
    doc_type: str,
    region: str,
    audience: str,
    effective_date: str,
    subdir: str,
    ext: str,
    product_code: str | None = None,
) -> DocMetadata:
    """Build validated metadata; the file name is always ``<doc_id>.<ext>``."""
    return DocMetadata(
        doc_id=doc_id,
        title=title,
        doc_type=doc_type,
        product_code=product_code,
        region=region,
        audience=audience,
        effective_date=effective_date,
        source_path=f"{subdir}/{doc_id}.{ext}",
    )


def _write_text(path: Path, text: str) -> None:
    """Write UTF-8 text with LF line endings on every platform."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


# ---------------------------------------------------------------- Markdown


def _md_cell(text: str) -> str:
    """Escape a table cell for Markdown."""
    return text.replace("|", "\\|")


def render_markdown(blocks: list[Block]) -> str:
    """Render blocks as GitHub-flavoured Markdown."""
    parts: list[str] = []
    for block in blocks:
        match block:
            case Heading(text, level):
                parts.append(f"{'#' * level} {text}")
            case Para(text):
                parts.append(text)
            case Bullets(items):
                parts.append("\n".join(f"- {item}" for item in items))
            case TableBlock(header, rows):
                lines = ["| " + " | ".join(_md_cell(c) for c in header) + " |"]
                lines.append("|" + "|".join("---" for _ in header) + "|")
                lines += ["| " + " | ".join(_md_cell(c) for c in row) + " |" for row in rows]
                parts.append("\n".join(lines))
            case PageBreakBlock():
                continue
    return "\n\n".join(parts) + "\n"


def write_markdown(out_dir: Path, meta: DocMetadata, blocks: list[Block]) -> DocMetadata:
    """Write a Markdown file with YAML front matter."""
    front = yaml.safe_dump(meta.model_dump(mode="json"), sort_keys=False, allow_unicode=False)
    _write_text(out_dir / meta.source_path, f"---\n{front}---\n\n{render_markdown(blocks)}")
    return meta


# -------------------------------------------------------------------- HTML


def write_html(out_dir: Path, meta: DocMetadata, body_html: str) -> DocMetadata:
    """Write an HTML page whose metadata lives in ``<meta>`` tags."""
    fields = meta.model_dump(mode="json")
    tags = "\n".join(
        f'  <meta name="{key}" content="{html.escape("" if value is None else str(value))}">'
        for key, value in fields.items()
    )
    page = (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '  <meta charset="utf-8">\n'
        f"  <title>{html.escape(meta.title)} | {STORE_NAME}</title>\n"
        f"{tags}\n"
        "</head>\n"
        "<body>\n"
        f"{body_html.rstrip()}\n"
        "</body>\n"
        "</html>\n"
    )
    _write_text(out_dir / meta.source_path, page)
    return meta


# --------------------------------------------------------------------- PDF


def _pdf_styles() -> StyleSheet1:
    """Sample styles plus a smaller style for table cells."""
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle("Cell", parent=styles["BodyText"], fontSize=8.5, leading=10.5))
    styles.add(
        ParagraphStyle("HeadCell", parent=styles["Cell"], fontName="Helvetica-Bold"),
    )
    return styles


def _pdf_table(block: TableBlock, styles: StyleSheet1, width: float) -> Table:
    """Build a wrapped, gridded table that fills the page width."""
    head = [Paragraph(xml_escape(c), styles["HeadCell"]) for c in block.header]
    body = [[Paragraph(xml_escape(c), styles["Cell"]) for c in row] for row in block.rows]
    table = Table([head, *body], colWidths=[width / len(block.header)] * len(block.header))
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    table.repeatRows = 1
    return table


def _pdf_flowables(block: Block, styles: StyleSheet1, width: float) -> list[Flowable]:
    """Convert one block to ReportLab flowables."""
    match block:
        case Heading(text, level):
            style = {1: "Title", 2: "Heading2", 3: "Heading3"}[level]
            return [Paragraph(xml_escape(text), styles[style])]
        case Para(text):
            return [Paragraph(xml_escape(text), styles["BodyText"])]
        case Bullets(items):
            return [
                Paragraph(xml_escape(item), styles["BodyText"], bulletText="-") for item in items
            ]
        case TableBlock():
            return [_pdf_table(block, styles, width), Spacer(1, 4 * mm)]
        case PageBreakBlock():
            return [PageBreak()]
    raise TypeError(f"Unknown block: {block!r}")


def write_pdf(out_dir: Path, meta: DocMetadata, blocks: list[Block]) -> DocMetadata:
    """Write a deterministic PDF and its ``.meta.json`` sidecar.

    ``invariant=1`` makes ReportLab use a fixed creation date and document ID,
    so identical input always produces identical bytes.
    """
    path = out_dir / meta.source_path
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(path),
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=meta.title,
        author=STORE_NAME,
        subject=meta.doc_id,
        creator=PDF_CREATOR,
        invariant=1,
    )
    styles = _pdf_styles()
    story = [f for block in blocks for f in _pdf_flowables(block, styles, doc.width)]
    doc.build(story)
    _write_text(sidecar_path(path), json.dumps(meta.model_dump(mode="json"), indent=2) + "\n")
    return meta


def sidecar_path(pdf_path: Path) -> Path:
    """Return the ``.meta.json`` path that belongs to a PDF."""
    return pdf_path.with_suffix(".meta.json")


# ---------------------------------------------------------------- Reading


def read_metadata(path: Path) -> DocMetadata:
    """Read the metadata embedded in (or stored beside) a generated document."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return DocMetadata.model_validate_json(sidecar_path(path).read_text(encoding="utf-8"))
    text = path.read_text(encoding="utf-8")
    if suffix == ".md":
        _, front, _ = text.split("---\n", 2)
        return DocMetadata.model_validate(yaml.safe_load(front))
    if suffix == ".html":
        fields = {k: html.unescape(v) or None for k, v in _META_TAG.findall(text)}
        return DocMetadata.model_validate(fields)
    raise ValueError(f"Unsupported document type: {path}")


def write_manifest(out_dir: Path, docs: list[DocMetadata]) -> Path:
    """Write ``manifest.json`` listing every document, sorted by ``doc_id``."""
    ordered = sorted(docs, key=lambda d: d.doc_id)
    payload = {
        "store": STORE_NAME,
        "document_count": len(ordered),
        "documents": [d.model_dump(mode="json") for d in ordered],
    }
    path = out_dir / "manifest.json"
    _write_text(path, json.dumps(payload, indent=2) + "\n")
    return path

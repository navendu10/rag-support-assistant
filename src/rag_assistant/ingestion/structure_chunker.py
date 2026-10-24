"""Structure-aware chunking (v1).

1. Split the Markdown on headings (h1-h3) with ``MarkdownHeaderTextSplitter``.
2. Merge sections shorter than ``min_chunk_chars`` into the next section.
3. Find table blocks: runs of consecutive lines that start with ``|``.
4. Sections longer than ``chunk_size`` are cut into units: whole tables, and text
   pieces from ``RecursiveCharacterTextSplitter`` (``chunk_size - min_chunk_chars``
   long, ``chunk_overlap`` overlap). Units are packed back together up to
   ``chunk_size``; only a chunk that contains a table may be longer.
5. Prefix every chunk with a context header ``[Title > Section > Subsection]``.

Inside a section all units are character spans of the section text, so a chunk's
body is always a contiguous slice of the source: tables are never cut, and
overlapping pieces are never duplicated inside one chunk.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter

from rag_assistant.config import StageConfig
from rag_assistant.ingestion.chunker import Chunk, make_chunk
from rag_assistant.ingestion.parsers import PAGE_MARKER
from rag_assistant.schemas import DocMetadata

HEADINGS = [("#", "h1"), ("##", "h2"), ("###", "h3")]
PATH_SEPARATOR = " > "


@dataclass(frozen=True)
class Section:
    """A heading section with page markers removed.

    ``page_changes`` lists (offset in ``text``, page) where a new page starts;
    ``start_page`` is the page in force at offset 0.
    """

    headings: tuple[str, ...]
    text: str
    start_page: int | None
    page_changes: tuple[tuple[int, int], ...] = ()

    def page_at(self, offset: int) -> int | None:
        """The page that the character at ``offset`` is on."""
        page = self.start_page
        for change_offset, change_page in self.page_changes:
            if change_offset <= offset:
                page = change_page
        return page


@dataclass(frozen=True)
class Span:
    """A half-open character range of a section's text."""

    start: int
    end: int
    has_table: bool = False

    def __len__(self) -> int:
        """Number of characters covered."""
        return self.end - self.start

    def union(self, other: Span) -> Span:
        """The smallest span covering both."""
        return Span(
            min(self.start, other.start),
            max(self.end, other.end),
            self.has_table or other.has_table,
        )


# ---------------------------------------------------------------- sections


def _strip_markers(
    content: str, current_page: int | None
) -> tuple[str, int | None, tuple[tuple[int, int], ...], int | None]:
    """Remove page markers; return (text, start page, page changes, page after section)."""
    start_page = current_page
    kept: list[str] = []
    changes: list[tuple[int, int]] = []
    offset = 0
    for raw_line in content.split("\n"):
        line = raw_line.rstrip()
        marker = PAGE_MARKER.match(line.strip())
        if marker:
            current_page = int(marker.group(1))
            if offset == 0:
                start_page = current_page
            else:
                changes.append((offset, current_page))
            continue
        kept.append(line)
        offset += len(line) + 1
    return "\n".join(kept).strip("\n"), start_page, tuple(changes), current_page


def split_sections(markdown: str) -> list[Section]:
    """Split Markdown on h1-h3 headings, keeping heading lines and tracking pages."""
    splitter = MarkdownHeaderTextSplitter(headers_to_split_on=HEADINGS, strip_headers=False)
    sections: list[Section] = []
    current_page: int | None = None
    for doc in splitter.split_text(markdown):
        text, start_page, changes, current_page = _strip_markers(doc.page_content, current_page)
        if not text.strip():
            continue
        headings = tuple(doc.metadata[key] for _, key in HEADINGS if key in doc.metadata)
        sections.append(Section(headings, text, start_page, changes))
    return sections


def _combine(first: Section, second: Section, headings: tuple[str, ...]) -> Section:
    """Concatenate two sections, shifting the second one's page changes."""
    shift = len(first.text) + 1
    second_changes = tuple((offset + shift, page) for offset, page in second.page_changes)
    if second.start_page != first.page_at(len(first.text)) and second.start_page is not None:
        second_changes = ((shift, second.start_page), *second_changes)
    return Section(
        headings=headings,
        text=f"{first.text}\n{second.text}",
        start_page=first.start_page,
        page_changes=first.page_changes + second_changes,
    )


def merge_small_sections(sections: Sequence[Section], min_chars: int) -> list[Section]:
    """Merge sections shorter than ``min_chars`` into the next (or, last, the previous) one.

    A merged section takes the headings of the section that receives the text.
    """
    merged: list[Section] = []
    carry: Section | None = None
    for section in sections:
        if carry is not None:
            section = _combine(carry, section, section.headings)
            carry = None
        if len(section.text) < min_chars:
            carry = section
            continue
        merged.append(section)
    if carry is not None:
        if merged:
            merged[-1] = _combine(merged[-1], carry, merged[-1].headings)
        else:
            merged.append(carry)
    return merged


# ------------------------------------------------------------------ tables


def is_table_line(line: str) -> bool:
    """Markdown table rows start with a pipe."""
    return line.lstrip().startswith("|")


def blocks(text: str) -> list[Span]:
    """Split text into alternating text and table blocks (runs of ``|`` lines)."""
    spans: list[Span] = []
    offset = 0
    current: Span | None = None
    for line in text.split("\n"):
        table = is_table_line(line)
        end = offset + len(line)
        if current is not None and current.has_table == table:
            current = Span(current.start, end, table)
        else:
            if current is not None:
                spans.append(current)
            current = Span(offset, end, table)
        offset = end + 1
    if current is not None:
        spans.append(current)
    return [s for s in spans if text[s.start : s.end].strip()]


# ---------------------------------------------------------------- packing


def _text_pieces(text: str, block: Span, splitter: RecursiveCharacterTextSplitter) -> list[Span]:
    """Recursively split one text block, mapping each piece back to its span."""
    source = text[block.start : block.end]
    pieces: list[Span] = []
    cursor = 0
    for piece in splitter.split_text(source):
        found = source.find(piece, cursor)
        if found < 0:  # the splitter strips whitespace; fall back to the cursor
            found = cursor
        pieces.append(Span(block.start + found, block.start + found + len(piece)))
        cursor = found + 1
    return pieces


def units(text: str, piece_size: int, splitter: RecursiveCharacterTextSplitter) -> list[Span]:
    """Whole tables plus text blocks small enough to pack (long ones are split)."""
    result: list[Span] = []
    for block in blocks(text):
        if block.has_table or len(block) <= piece_size:
            result.append(block)
        else:
            result.extend(_text_pieces(text, block, splitter))
    return result


def pack(spans: Sequence[Span], max_chars: int, min_chars: int) -> list[Span]:
    """Greedily join consecutive units up to ``max_chars``.

    A buffer shorter than ``min_chars`` always joins the next unit, and a short
    final chunk is merged into the previous one when that stays within bounds (or
    either contains a table).
    """
    chunks: list[Span] = []
    buffer: Span | None = None
    for span in spans:
        if buffer is None:
            buffer = span
            continue
        joined = buffer.union(span)
        if len(joined) <= max_chars or len(buffer) < min_chars:
            buffer = joined
        else:
            chunks.append(buffer)
            buffer = span
    if buffer is not None:
        chunks.append(buffer)
    if len(chunks) > 1 and len(chunks[-1]) < min_chars:
        joined = chunks[-2].union(chunks[-1])
        if len(joined) <= max_chars or joined.has_table:
            chunks[-2:] = [joined]
    return chunks


def section_spans(
    section: Section, stage: StageConfig, splitter: RecursiveCharacterTextSplitter
) -> list[Span]:
    """Spans of one section's chunks; short sections stay whole."""
    text = section.text
    if len(text) <= stage.chunk_size:
        return [Span(0, len(text), any(b.has_table for b in blocks(text)))]
    piece_size = stage.chunk_size - stage.min_chunk_chars
    return pack(units(text, piece_size, splitter), stage.chunk_size, stage.min_chunk_chars)


# ----------------------------------------------------------------- headers


def _normalise_heading(text: str) -> str:
    """Compare headings case- and whitespace-insensitively."""
    return " ".join(text.lower().split())


def heading_path(title: str, headings: Sequence[str]) -> list[str]:
    """``[title, h1, h2, h3]`` without headings that just repeat the document title."""
    parts = [title]
    for heading in headings:
        seen = {_normalise_heading(title), _normalise_heading(parts[-1])}
        if heading and _normalise_heading(heading) not in seen:
            parts.append(heading)
    return parts


def context_header(path: Sequence[str]) -> str:
    """The ``[Title > Section > Subsection]`` line prefixed to every chunk."""
    return "[" + PATH_SEPARATOR.join(path) + "]"


# ------------------------------------------------------------------- entry


def make_section_splitter(stage: StageConfig) -> RecursiveCharacterTextSplitter:
    """Splitter for over-long text inside a section (keyword arguments!)."""
    return RecursiveCharacterTextSplitter(
        chunk_size=stage.chunk_size - stage.min_chunk_chars,
        chunk_overlap=stage.chunk_overlap,
    )


def chunk_markdown(markdown: str, meta: DocMetadata, stage: StageConfig) -> list[Chunk]:
    """Turn one parsed document into structure-aware chunks with full metadata."""
    splitter = make_section_splitter(stage)
    sections = merge_small_sections(split_sections(markdown), stage.min_chunk_chars)
    chunks: list[Chunk] = []
    for section in sections:
        path = heading_path(meta.title, section.headings)
        header = context_header(path)
        for span in section_spans(section, stage, splitter):
            body = section.text[span.start : span.end].strip("\n")
            chunks.append(
                make_chunk(
                    meta,
                    f"{header}\n{body}",
                    index=len(chunks),
                    section=PATH_SEPARATOR.join(path[1:]),
                    page=section.page_at(span.start),
                )
            )
    return chunks


def chunk_body(chunk: Chunk) -> str:
    """A structure-aware chunk's text without its context header line."""
    return chunk.text.split("\n", 1)[1] if "\n" in chunk.text else ""

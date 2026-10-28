"""What the cross-encoder reads for each chunk (the LLM still gets the original text).

ms-marco-style rerankers are trained on prose passages and score Markdown pipe tables
close to zero, and long tables push the (question, chunk) pair past the 512-token
input limit. These variants change only the reranker's input:

- ``chunk``: the chunk text as stored, with its ``[Title > Section]`` header.
- ``body``: the same text without the header line.
- ``linear``: every table row rewritten as one sentence using the column names and
  the header's title and section ("... Product category Audio; Condition Opened;
  Return window 15 days ...").
- ``groups``: long tables split into groups of a few rows, the column header repeated
  in each; the chunk scores as its best group. No group comes near 512 tokens.
- ``linear_groups``: row groups, each linearised.

Every variant returns a list of texts to score; a chunk's score is the best of them.
"""

from __future__ import annotations

from typing import Literal

from rag_assistant.ingestion.structure_chunker import blocks

RerankInput = Literal["chunk", "body", "linear", "groups", "linear_groups"]
ROWS_PER_GROUP = 4


def split_header(text: str) -> tuple[str, str]:
    """``(header, body)``: the first line if it is a ``[...]`` context header, else ``""``."""
    first, newline, rest = text.partition("\n")
    if first.startswith("[") and first.rstrip().endswith("]"):
        return first, rest
    return "", text


def _cells(line: str) -> list[str]:
    """Cell values of one Markdown table row."""
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _is_separator(line: str) -> bool:
    """``|---|:--:|`` rows between the column names and the data."""
    return all(set(c) <= set("-: ") for c in _cells(line))


def parse_table(table: str) -> tuple[list[str], list[list[str]]]:
    """Column names and data rows of a Markdown table (separator rows dropped)."""
    lines = [line for line in table.split("\n") if line.strip()]
    if not lines:
        return [], []
    columns = _cells(lines[0])
    rows = [_cells(line) for line in lines[1:] if not _is_separator(line)]
    return columns, rows


def _context(header: str) -> str:
    """``Title > last section`` from a ``[Title > ... > Section]`` header."""
    parts = [p.strip() for p in header.strip("[] ").split(" > ") if p.strip()]
    if not parts:
        return ""
    return parts[0] if len(parts) == 1 else f"{parts[0]} > {parts[-1]}"


def row_sentence(context: str, columns: list[str], row: list[str]) -> str:
    """One table row as a sentence: ``Context: Col1 v1; Col2 v2.``"""
    pairs = [f"{col} {val}" for col, val in zip(columns, row, strict=False) if val]
    prefix = f"{context}: " if context else ""
    return prefix + "; ".join(pairs) + "."


def linearise_tables(text: str) -> str:
    """``text`` with every table replaced by one sentence per row (prose kept)."""
    header, body = split_header(text)
    context = _context(header)
    parts = [header] if header else []
    for span in blocks(body):
        piece = body[span.start : span.end]
        if not span.has_table:
            parts.append(piece.strip())
            continue
        columns, rows = parse_table(piece)
        parts += [row_sentence(context, columns, row) for row in rows]
    return "\n".join(p for p in parts if p)


def row_groups(text: str, rows_per_group: int = ROWS_PER_GROUP) -> list[str]:
    """Copies of ``text`` that each keep one group of rows from each table.

    The header, the prose and every table's column names are repeated in each copy.
    A chunk without tables, or whose tables are no longer than one group, is returned
    unchanged as a single text.
    """
    header, body = split_header(text)
    pieces = blocks(body)
    tables = [(n, parse_table(body[s.start : s.end])) for n, s in enumerate(pieces) if s.has_table]
    longest = max((len(rows) for _, (_, rows) in tables), default=0)
    if longest <= rows_per_group:
        return [text]
    groups = []
    for start in range(0, longest, rows_per_group):
        parts = [header] if header else []
        by_index = dict(tables)
        for n, span in enumerate(pieces):
            if n not in by_index:
                parts.append(body[span.start : span.end].strip())
                continue
            columns, rows = by_index[n]
            chunk_rows = rows[start : start + rows_per_group]
            if chunk_rows:
                lines = [columns, ["---"] * len(columns), *chunk_rows]
                parts.append("\n".join("| " + " | ".join(r) + " |" for r in lines))
        groups.append("\n".join(p for p in parts if p))
    return groups


def reranker_inputs(text: str, variant: RerankInput) -> list[str]:
    """The texts the cross-encoder scores for one chunk; its score is the best of them."""
    if variant == "chunk":
        return [text]
    if variant == "body":
        return [split_header(text)[1]]
    if variant == "linear":
        return [linearise_tables(text)]
    if variant == "groups":
        return row_groups(text)
    return [linearise_tables(group) for group in row_groups(text)]

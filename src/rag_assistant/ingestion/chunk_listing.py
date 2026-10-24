"""Human-readable chunk listings for ``scripts/show_chunks.py``.

Chunks are dataclasses whose fields differ by stage (v0 has fewer metadata fields
than later stages), so every field that exists is printed, whatever it is.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import fields, is_dataclass
from typing import Any

# A structure-aware context header is the first line, e.g. "[Title > Section]".
_HEADER = re.compile(r"^\[[^\n]*\]$")
SKIPPED_FIELDS = {"text"}
RULE = "-" * 72


def context_header(text: str) -> str | None:
    """The chunk's ``[Title > Section]`` header line, or None if it has none (v0)."""
    first_line = text.split("\n", 1)[0]
    return first_line if _HEADER.match(first_line) else None


def metadata_fields(chunk: Any) -> dict[str, object]:
    """Every dataclass field of ``chunk`` except its text, in declaration order."""
    if not is_dataclass(chunk):
        raise TypeError(f"expected a dataclass chunk, got {type(chunk).__name__}")
    return {f.name: getattr(chunk, f.name) for f in fields(chunk) if f.name not in SKIPPED_FIELDS}


def filter_chunks(chunks: Sequence[Any], grep: str | None) -> list[Any]:
    """Chunks whose text contains ``grep`` (case-insensitive); all of them if None."""
    if not grep:
        return list(chunks)
    needle = grep.lower()
    return [c for c in chunks if needle in c.text.lower()]


def format_chunk(chunk: Any) -> str:
    """One chunk: a rule with index and length, its header, every metadata field, its text."""
    meta = metadata_fields(chunk)
    index = meta.get("chunk_index", "?")
    width = max(len(name) for name in [*meta, "header"])
    lines = [f"{RULE}\nchunk {index} | {len(chunk.text)} chars"]
    lines.append(f"{'header':<{width}} : {context_header(chunk.text) or '(none)'}")
    lines += [f"{name:<{width}} : {value}" for name, value in meta.items()]
    lines += ["text:", chunk.text]
    return "\n".join(lines)


def format_listing(chunks: Sequence[Any], stage: str, doc_id: str, grep: str | None = None) -> str:
    """The full listing for one document, with a summary line first."""
    shown = filter_chunks(chunks, grep)
    match = f", {len(shown)} matching {grep!r}" if grep else ""
    summary = f"{doc_id} | stage {stage} | {len(chunks)} chunks{match}"
    body = [format_chunk(c) for c in shown]
    if grep and not shown:
        body.append(f"No chunk contains {grep!r}.")
    return "\n".join([summary, *body]) + "\n"

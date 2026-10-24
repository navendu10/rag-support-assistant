"""Chunk model plus the fixed-size (v0) chunker."""

from __future__ import annotations

from dataclasses import dataclass

from langchain_text_splitters import RecursiveCharacterTextSplitter

from rag_assistant.config import StageConfig
from rag_assistant.schemas import DocMetadata


@dataclass(frozen=True)
class Chunk:
    """One piece of a document plus the metadata stored with it.

    The metadata is stored for evaluation and inspection only; no stage up to v1
    uses it for retrieval. ``section`` and ``page`` are only filled by the
    structure-aware chunker (``page`` only for PDFs).
    """

    text: str
    doc_id: str
    title: str
    chunk_index: int
    audience: str
    region: str
    doc_type: str
    product_code: str | None = None
    effective_date: str | None = None
    section: str = ""
    page: int | None = None


def make_splitter(stage: StageConfig) -> RecursiveCharacterTextSplitter:
    """Build the fixed-size splitter for v0.

    Keyword arguments matter: the first positional parameter is ``separators``.
    """
    return RecursiveCharacterTextSplitter(
        chunk_size=stage.chunk_size,
        chunk_overlap=stage.chunk_overlap,
    )


def make_chunk(
    meta: DocMetadata, text: str, index: int, section: str = "", page: int | None = None
) -> Chunk:
    """A chunk that inherits every field of its document's metadata."""
    return Chunk(
        text=text,
        doc_id=meta.doc_id,
        title=meta.title,
        chunk_index=index,
        audience=meta.audience,
        region=meta.region,
        doc_type=meta.doc_type,
        product_code=meta.product_code,
        effective_date=meta.effective_date.isoformat(),
        section=section,
        page=page,
    )


def split_document(
    text: str, meta: DocMetadata, splitter: RecursiveCharacterTextSplitter
) -> list[Chunk]:
    """Split one document's text into numbered fixed-size chunks (v0)."""
    return [make_chunk(meta, piece, i) for i, piece in enumerate(splitter.split_text(text))]

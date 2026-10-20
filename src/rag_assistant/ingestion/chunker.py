"""Split document text into chunks according to the stage config."""

from __future__ import annotations

from dataclasses import dataclass

from langchain_text_splitters import RecursiveCharacterTextSplitter

from rag_assistant.config import StageConfig
from rag_assistant.schemas import DocMetadata


@dataclass(frozen=True)
class Chunk:
    """One piece of a document plus the metadata stored with it.

    The metadata is stored for evaluation only; v0 retrieval never filters on it.
    """

    text: str
    doc_id: str
    title: str
    chunk_index: int
    audience: str
    region: str
    doc_type: str


def make_splitter(stage: StageConfig) -> RecursiveCharacterTextSplitter:
    """Build the fixed-size splitter for v0.

    Keyword arguments matter: the first positional parameter is ``separators``.
    """
    return RecursiveCharacterTextSplitter(
        chunk_size=stage.chunk_size,
        chunk_overlap=stage.chunk_overlap,
    )


def split_document(
    text: str, meta: DocMetadata, splitter: RecursiveCharacterTextSplitter
) -> list[Chunk]:
    """Split one document's text into numbered chunks."""
    return [
        Chunk(
            text=piece,
            doc_id=meta.doc_id,
            title=meta.title,
            chunk_index=i,
            audience=meta.audience,
            region=meta.region,
            doc_type=meta.doc_type,
        )
        for i, piece in enumerate(splitter.split_text(text))
    ]

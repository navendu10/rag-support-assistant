"""Rebuild a stored run's context texts offline, for judges back-filled later (Session 8).

``results.jsonl`` stores each context chunk by ``doc_id`` and ``chunk_index`` (the first
``top_k`` of ``retrieved``), not its text. Chunking is deterministic, so re-chunking
``data/generated`` with the stage's settings gives the same texts. No Weaviate key is
involved, and nothing is regenerated. Every rebuilt chunk is checked against the stored
section, so a changed chunker or knowledge base fails loudly instead of judging an answer
against the wrong text.
"""

from __future__ import annotations

from pathlib import Path

from rag_assistant.config import StageConfig
from rag_assistant.eval.metrics import ResultRow
from rag_assistant.ingestion.chunker import Chunk
from rag_assistant.ingestion.pipeline import chunk_documents

ChunkKey = tuple[str, int]


class ContextMismatchError(ValueError):
    """A stored context chunk doesn't match the rebuilt one."""


def chunk_lookup(stage: StageConfig, generated_dir: Path) -> dict[ChunkKey, Chunk]:
    """Every chunk the stage's ingestion would build, by ``(doc_id, chunk_index)``."""
    return {(c.doc_id, c.chunk_index): c for c in chunk_documents(stage, generated_dir)}


def stored_context(row: ResultRow, lookup: dict[ChunkKey, Chunk]) -> list[Chunk]:
    """The chunks ``row``'s answer was generated from, rebuilt and checked.

    Raises:
        ContextMismatchError: if a chunk is missing, or its doc or section differ from
            what the row recorded.
    """
    refs = row.retrieved[: len(row.context_doc_ids)]
    if [r.doc_id for r in refs] != row.context_doc_ids:
        raise ContextMismatchError(f"{row.id}: context doc_ids are not the top retrieved chunks")
    chunks = []
    for ref in refs:
        chunk = lookup.get((ref.doc_id, ref.chunk_index))
        if chunk is None:
            raise ContextMismatchError(f"{row.id}: no chunk {ref.doc_id}#{ref.chunk_index}")
        if (ref.section or "") != chunk.section:
            raise ContextMismatchError(
                f"{row.id}: {ref.doc_id}#{ref.chunk_index} section {ref.section!r} "
                f"!= rebuilt {chunk.section!r}"
            )
        chunks.append(chunk)
    return chunks

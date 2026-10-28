"""Ingest every document in the manifest into the stage's Weaviate collection."""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path

from rag_assistant.config import Settings, StageConfig
from rag_assistant.embeddings import Embedder
from rag_assistant.ingestion.chunker import Chunk, make_splitter, split_document
from rag_assistant.ingestion.loaders import load_text
from rag_assistant.ingestion.parsers import parse_structured
from rag_assistant.ingestion.store import connect, insert_chunks, recreate_collection
from rag_assistant.ingestion.structure_chunker import chunk_markdown
from rag_assistant.schemas import DocMetadata, load_manifest


@dataclass(frozen=True)
class IngestStats:
    """What an ingestion run stored."""

    collection: str
    documents: int
    chunks: int
    chunks_by_audience: dict[str, int]


def load_documents(generated_dir: Path) -> list[DocMetadata]:
    """Read the manifest, with a helpful error if the data has not been generated."""
    manifest = generated_dir / "manifest.json"
    if not manifest.is_file():
        raise FileNotFoundError(f"{manifest} missing. Run: python scripts/generate_data.py")
    return load_manifest(manifest)


def chunk_documents(
    stage: StageConfig, generated_dir: Path, doc_ids: Collection[str] | None = None
) -> list[Chunk]:
    """Load and split manifest documents exactly as ingestion does.

    Args:
        stage: the stage whose chunking settings apply.
        generated_dir: folder holding manifest.json and the documents.
        doc_ids: only these documents (all of them when None).

    Raises:
        KeyError: if a requested doc_id is not in the manifest.
    """
    docs = load_documents(generated_dir)
    if doc_ids is not None:
        missing = sorted(set(doc_ids) - {d.doc_id for d in docs})
        if missing:
            raise KeyError(f"Unknown doc_id(s): {', '.join(missing)}")
        docs = [d for d in docs if d.doc_id in doc_ids]
    return [chunk for meta in docs for chunk in chunk_document(stage, meta, generated_dir)]


def chunk_document(stage: StageConfig, meta: DocMetadata, generated_dir: Path) -> list[Chunk]:
    """Parse and chunk one document the way the stage says.

    ``fixed`` (v0): plain text and fixed-size chunks. ``structure`` (v1+): Markdown
    parsing and structure-aware chunks with context headers.
    """
    if stage.chunking == "structure":
        return chunk_markdown(parse_structured(meta, generated_dir), meta, stage)
    return split_document(load_text(meta, generated_dir), meta, make_splitter(stage))


def build_chunks(stage: StageConfig, settings: Settings) -> list[Chunk]:
    """Load and split every manifest document (public AND internal: no stage filters yet)."""
    return chunk_documents(stage, settings.paths.resolve(settings.paths.generated_dir))


def ingest(stage: StageConfig, settings: Settings, embedder: Embedder | None = None) -> IngestStats:
    """Replace the stage collection with freshly embedded chunks.

    Raises:
        ValueError: if the stage reuses another stage's index; ingest the owner instead.
    """
    if stage.reuses_index:
        raise ValueError(
            f"Stage {stage.name} reuses {stage.collection_name}; "
            f"ingest stage {stage.index_owner} instead"
        )
    chunks = build_chunks(stage, settings)
    embedder = embedder or Embedder(settings.embedding)
    vectors = embedder.embed([c.text for c in chunks])
    with connect(settings.weaviate) as client:
        collection = recreate_collection(client, stage.collection_name)
        insert_chunks(collection, chunks, vectors)
    return IngestStats(
        collection=stage.collection_name,
        documents=len({c.doc_id for c in chunks}),
        chunks=len(chunks),
        chunks_by_audience=dict(Counter(c.audience for c in chunks)),
    )

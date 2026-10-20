"""Ingest every document in the manifest into the stage's Weaviate collection."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from rag_assistant.config import Settings, StageConfig
from rag_assistant.embeddings import Embedder
from rag_assistant.ingestion.chunker import Chunk, make_splitter, split_document
from rag_assistant.ingestion.loaders import load_text
from rag_assistant.ingestion.store import connect, insert_chunks, recreate_collection
from rag_assistant.schemas import load_manifest


@dataclass(frozen=True)
class IngestStats:
    """What an ingestion run stored."""

    collection: str
    documents: int
    chunks: int
    chunks_by_audience: dict[str, int]


def build_chunks(stage: StageConfig, settings: Settings) -> list[Chunk]:
    """Load and split every manifest document (public AND internal, as v0 does)."""
    generated = settings.paths.resolve(settings.paths.generated_dir)
    manifest = generated / "manifest.json"
    if not manifest.is_file():
        raise FileNotFoundError(f"{manifest} missing. Run: python scripts/generate_data.py")
    splitter = make_splitter(stage)
    return [
        chunk
        for meta in load_manifest(manifest)
        for chunk in split_document(load_text(meta, generated), meta, splitter)
    ]


def ingest(stage: StageConfig, settings: Settings, embedder: Embedder | None = None) -> IngestStats:
    """Replace the stage collection with freshly embedded chunks."""
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

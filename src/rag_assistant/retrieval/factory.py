"""Pick the retriever a stage asks for (``search: vector`` or ``search: hybrid``)."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from weaviate.collections import Collection

from rag_assistant.config import StageConfig
from rag_assistant.embeddings import Embedder
from rag_assistant.retrieval.codes import CodeDetector
from rag_assistant.retrieval.hybrid import HybridRetriever
from rag_assistant.retrieval.vector import RetrievedChunk, VectorRetriever


class Retriever(Protocol):
    """Anything that returns the best ``k`` chunks for a query, best first."""

    def search(self, query: str, k: int) -> list[RetrievedChunk]:
        """Return the ``k`` best chunks for ``query``."""
        ...


def make_retriever(
    stage: StageConfig, collection: Collection, embedder: Embedder, manifest_path: Path
) -> Retriever:
    """Vector retriever for ``search: vector``; hybrid (with code detection) otherwise."""
    if stage.hybrid is None:
        return VectorRetriever(collection, embedder)
    return HybridRetriever(
        collection, embedder, stage.hybrid, CodeDetector.from_manifest(manifest_path)
    )

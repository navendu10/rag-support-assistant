"""The RAG pipeline for one stage: retrieve, then generate.

In v0 the ``user`` and ``history`` arguments are accepted but ignored
(``use_history=false``, ``region_filter=false``, ``access_control=false``). That is
exactly what lets failure #4 (vague queries, wrong region) and #5 (leaks) appear.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from types import TracebackType

import weaviate

from rag_assistant.config import Settings, StageConfig, get_settings, load_prompts, load_stage
from rag_assistant.embeddings import Embedder
from rag_assistant.generation.naive import NaiveGenerator
from rag_assistant.ingestion.store import open_client
from rag_assistant.llm import make_chat_model
from rag_assistant.retrieval.vector import RetrievedChunk, VectorRetriever
from rag_assistant.schemas import Turn, User


@dataclass(frozen=True)
class Answer:
    """The pipeline's output for one question."""

    text: str
    context: list[RetrievedChunk]
    retrieved: list[RetrievedChunk]
    timings_ms: dict[str, float] = field(default_factory=dict)


def _ms_since(start: float) -> float:
    """Milliseconds elapsed since ``start`` (a ``perf_counter`` value)."""
    return round((time.perf_counter() - start) * 1000, 1)


class Pipeline:
    """Retrieve-then-generate pipeline configured by a ``StageConfig``.

    Use as a context manager so the Weaviate connection is closed.
    """

    def __init__(
        self,
        stage: StageConfig,
        retriever: VectorRetriever,
        generator: NaiveGenerator,
        client: weaviate.WeaviateClient | None = None,
    ) -> None:
        """Assemble a pipeline from already-built parts."""
        stage.ensure_supported()
        self.stage = stage
        self._retriever = retriever
        self._generator = generator
        self._client = client

    @classmethod
    def from_stage(cls, stage: str | StageConfig, settings: Settings | None = None) -> Pipeline:
        """Build the pipeline for a stage name (or config) using the real services."""
        settings = settings or get_settings()
        stage = load_stage(stage) if isinstance(stage, str) else stage
        client = open_client(settings.weaviate)
        if not client.collections.exists(stage.collection_name):
            client.close()
            raise RuntimeError(
                f"Collection {stage.collection_name} not found. "
                f"Run: python scripts/ingest.py --stage {stage.name}"
            )
        retriever = VectorRetriever(
            client.collections.get(stage.collection_name), Embedder(settings.embedding)
        )
        generator = NaiveGenerator(
            make_chat_model(settings, settings.llm.generation_model),
            load_prompts()["naive_answer"],
        )
        return cls(stage, retriever, generator, client)

    def __enter__(self) -> Pipeline:
        """Enter the context manager."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close the Weaviate connection."""
        self.close()

    def close(self) -> None:
        """Release the Weaviate client, if this pipeline owns one."""
        if self._client is not None:
            self._client.close()
            self._client = None

    def retrieve(self, question: str, k: int) -> list[RetrievedChunk]:
        """v0: vector search on the raw latest message only."""
        return self._retriever.search(question, k)

    def answer(
        self,
        question: str,
        user: User | None = None,
        history: Sequence[Turn] = (),
        retrieve_k: int | None = None,
    ) -> Answer:
        """Answer ``question``. ``user`` and ``history`` are ignored in v0.

        Args:
            question: the latest customer message.
            user: who is asking (unused until region filtering / access control).
            history: earlier turns (unused until ``use_history``).
            retrieve_k: retrieve at least this many chunks (for recall@k); only the
                first ``top_k`` are used as context.
        """
        del user, history  # deliberately unused in v0
        start = time.perf_counter()
        retrieved = self.retrieve(question, max(self.stage.top_k, retrieve_k or 0))
        retrieve_ms = _ms_since(start)
        context = retrieved[: self.stage.top_k]
        gen_start = time.perf_counter()
        text = self._generator.generate(question, context)
        return Answer(
            text=text,
            context=context,
            retrieved=retrieved,
            timings_ms={
                "retrieve": retrieve_ms,
                "generate": _ms_since(gen_start),
                "total": _ms_since(start),
            },
        )

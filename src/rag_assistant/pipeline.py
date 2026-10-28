"""The RAG pipeline for one stage: retrieve (and optionally rerank), then generate.

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
from rag_assistant.llm import make_chat_model, timed_calls
from rag_assistant.retrieval.factory import Retriever, make_retriever
from rag_assistant.retrieval.rerank import Reranker
from rag_assistant.retrieval.vector import RetrievedChunk
from rag_assistant.schemas import Turn, User


@dataclass(frozen=True)
class Answer:
    """The pipeline's output for one question."""

    text: str
    context: list[RetrievedChunk]
    retrieved: list[RetrievedChunk]
    timings_ms: dict[str, float] = field(default_factory=dict)
    candidates: list[RetrievedChunk] = field(default_factory=list)


@dataclass(frozen=True)
class Retrieval:
    """Ranked chunks, the search candidates they came from, and how long each step took."""

    ranked: list[RetrievedChunk]
    candidates: list[RetrievedChunk]
    timings_ms: dict[str, float]


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
        retriever: Retriever,
        generator: NaiveGenerator,
        client: weaviate.WeaviateClient | None = None,
        reranker: Reranker | None = None,
        generation_model: str | None = None,
    ) -> None:
        """Assemble a pipeline from already-built parts (``reranker`` iff ``stage.rerank``)."""
        stage.ensure_supported()
        if stage.rerank != (reranker is not None):
            raise ValueError("pass a reranker exactly when the stage has rerank: true")
        self.stage = stage
        self._retriever = retriever
        self._generator = generator
        self._client = client
        self._reranker = reranker
        self._generation_model = generation_model  # whose throttle waits to exclude

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
                f"Run: python scripts/ingest.py --stage {stage.index_owner}"
            )
        retriever = make_retriever(
            stage,
            client.collections.get(stage.collection_name),
            Embedder(settings.embedding),
            settings.paths.resolve(settings.paths.generated_dir) / "manifest.json",
        )
        generator = NaiveGenerator(
            make_chat_model(settings, settings.llm.generation_model),
            load_prompts()["naive_answer"],
        )
        reranker = None
        if stage.rerank:
            reranker = Reranker(str(stage.rerank_model))
            reranker.warm_up()  # load once, so per-question rerank timings exclude start-up
        return cls(stage, retriever, generator, client, reranker, settings.llm.generation_model)

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

    def retrieve_ranked(self, question: str, k: int) -> Retrieval:
        """The best ``k`` chunks for ``question`` (raw latest message only), with timings.

        Without reranking this is the search result. With reranking, ``fetch_k``
        candidates are searched, reordered by the cross-encoder, and cut to ``k``.
        """
        start = time.perf_counter()
        if self._reranker is None:
            ranked = self._retriever.search(question, k)
            return Retrieval(ranked, [], {"retrieve": _ms_since(start)})
        candidates = self._retriever.search(question, max(k, int(self.stage.fetch_k or 0)))
        retrieve_ms = _ms_since(start)
        rerank_start = time.perf_counter()
        ranked = self._reranker.rerank(
            question,
            candidates,
            self.stage.rerank_fusion or "replace",
            self.stage.rerank_blend_weight if self.stage.rerank_blend_weight is not None else 0.5,
        )[:k]
        return Retrieval(
            ranked, candidates, {"retrieve": retrieve_ms, "rerank": _ms_since(rerank_start)}
        )

    def retrieve(self, question: str, k: int) -> list[RetrievedChunk]:
        """The best ``k`` chunks for ``question`` (searched, then reranked if enabled)."""
        return self.retrieve_ranked(question, k).ranked

    def answer(
        self,
        question: str,
        user: User | None = None,
        history: Sequence[Turn] = (),
        retrieve_k: int | None = None,
    ) -> Answer:
        """Answer ``question``. ``user`` and ``history`` are still ignored.

        Args:
            question: the latest customer message.
            user: who is asking (unused until region filtering / access control).
            history: earlier turns (unused until ``use_history``).
            retrieve_k: retrieve at least this many chunks (for recall@k); only the
                first ``top_k`` are used as context.
        """
        del user, history  # deliberately unused until Sessions 6 and 7
        start = time.perf_counter()
        retrieval = self.retrieve_ranked(question, max(self.stage.top_k, retrieve_k or 0))
        context = retrieval.ranked[: self.stage.top_k]
        with timed_calls(self._generation_model) as gen:
            text = self._generator.generate(question, context)
        return Answer(
            text=text,
            context=context,
            retrieved=retrieval.ranked,
            timings_ms={
                **retrieval.timings_ms,
                "generate": gen.net_ms,  # excludes throttle and retry waits
                "generate_wait": gen.wait_ms,
                "generate_requests": float(gen.requests),  # 0 = answered from the cache
                "total": _ms_since(start),  # wall clock, waits included
            },
            candidates=retrieval.candidates,
        )

"""The RAG pipeline for one stage: (rewrite,) retrieve, (rerank,) then generate.

Up to v3 the ``user`` and ``history`` arguments are accepted but ignored. That is
exactly what lets failure #4 (vague queries, wrong region) and #5 (leaks) appear.

From Session 6:

- ``query_rewrite`` (+ ``use_history``): the small model rewrites the latest message
  with the chat history. ``search_query`` goes to hybrid search (with the extracted
  product codes for dynamic alpha); ``standalone_question`` goes to the reranker and to
  generation.
- ``region_filter``: customers search only their profile's region and ALL; the filter
  comes from ``user``, never from the message.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from types import TracebackType

import weaviate

from rag_assistant.config import (
    Settings,
    StageConfig,
    get_settings,
    load_prompts,
    load_stage,
)
from rag_assistant.embeddings import Embedder
from rag_assistant.generation.naive import NaiveGenerator
from rag_assistant.ingestion.store import open_client
from rag_assistant.llm import make_chat_model, timed_calls
from rag_assistant.query.rewrite import Catalogue, QueryRewriter, RewriteResult, json_completer
from rag_assistant.retrieval.factory import Retriever, make_retriever
from rag_assistant.retrieval.filters import build_region_filter, region_values
from rag_assistant.retrieval.rerank import Reranker
from rag_assistant.retrieval.vector import RetrievedChunk
from rag_assistant.schemas import Turn, User, load_catalogue


@dataclass(frozen=True)
class Answer:
    """The pipeline's output for one question."""

    text: str
    context: list[RetrievedChunk]
    retrieved: list[RetrievedChunk]
    timings_ms: dict[str, float] = field(default_factory=dict)
    candidates: list[RetrievedChunk] = field(default_factory=list)
    rewrite: RewriteResult | None = None
    region_filter: tuple[str, ...] | None = None


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
        rewriter: QueryRewriter | None = None,
        rewrite_model: str | None = None,
    ) -> None:
        """Assemble a pipeline from already-built parts.

        Pass ``reranker`` exactly when ``stage.rerank`` and ``rewriter`` exactly when
        ``stage.query_rewrite``. The model names say whose throttle waits to exclude.
        """
        stage.ensure_supported()
        if stage.rerank != (reranker is not None):
            raise ValueError("pass a reranker exactly when the stage has rerank: true")
        if stage.query_rewrite != (rewriter is not None):
            raise ValueError("pass a rewriter exactly when the stage has query_rewrite: true")
        self.stage = stage
        self._retriever = retriever
        self._generator = generator
        self._client = client
        self._reranker = reranker
        self._generation_model = generation_model
        self._rewriter = rewriter
        self._rewrite_model = rewrite_model

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
        rewriter = make_rewriter(settings) if stage.query_rewrite else None
        return cls(
            stage,
            retriever,
            generator,
            client,
            reranker,
            settings.llm.generation_model,
            rewriter,
            settings.llm.small_model,
        )

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

    def retrieve_ranked(
        self,
        question: str,
        k: int,
        *,
        rerank_query: str | None = None,
        extra_codes: Sequence[str] = (),
        regions: tuple[str, ...] | None = None,
    ) -> Retrieval:
        """The best ``k`` chunks for ``question``, with timings.

        Without reranking this is the search result. With reranking, ``fetch_k``
        candidates are searched, reordered by the cross-encoder, and cut to ``k``.

        Args:
            question: the search text (the rewriter's search query, if rewriting).
            k: how many ranked chunks to return.
            rerank_query: the text the cross-encoder scores against (default ``question``).
            extra_codes: product codes from the rewriter, for dynamic alpha.
            regions: allowed ``region`` values, applied inside the search (None: no filter).
        """
        start = time.perf_counter()
        codes, filters = tuple(extra_codes), build_region_filter(regions)
        if self._reranker is None:
            ranked = self._retriever.search(question, k, extra_codes=codes, filters=filters)
            return Retrieval(ranked, [], {"retrieve": _ms_since(start)})
        fetch = max(k, int(self.stage.fetch_k or 0))
        candidates = self._retriever.search(question, fetch, extra_codes=codes, filters=filters)
        retrieve_ms = _ms_since(start)
        rerank_start = time.perf_counter()
        ranked = self._reranker.rerank(
            rerank_query or question,
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

    def regions_for(self, user: User | None) -> tuple[str, ...] | None:
        """The region filter values for ``user``, or None when the stage doesn't filter.

        Only the profile is read: the message can't change the filter.
        """
        if not self.stage.region_filter:
            return None
        if user is None:
            raise ValueError(f"stage {self.stage.name} filters by region, so it needs a user")
        return region_values(user)

    def rewrite(
        self, question: str, history: Sequence[Turn] = ()
    ) -> tuple[RewriteResult, dict[str, float]]:
        """Rewrite ``question`` (with ``history`` if ``use_history``); result and timings."""
        if self._rewriter is None:
            raise ValueError(f"stage {self.stage.name} has query_rewrite: false")
        turns = history if self.stage.use_history else ()
        with timed_calls(self._rewrite_model) as call:
            result = self._rewriter.rewrite(question, turns)
        timings = {
            "rewrite": call.net_ms,  # excludes throttle and retry waits
            "rewrite_wait": call.wait_ms,
            "rewrite_requests": float(call.requests),  # 0 = answered from the cache
        }
        return result, timings

    def answer(
        self,
        question: str,
        user: User | None = None,
        history: Sequence[Turn] = (),
        retrieve_k: int | None = None,
    ) -> Answer:
        """Answer ``question`` for ``user`` after the earlier turns in ``history``.

        Args:
            question: the latest customer message.
            user: who is asking; the region filter reads their profile (access control
                arrives in Session 7).
            history: earlier turns; only the rewriter reads them, when ``use_history``.
            retrieve_k: retrieve at least this many chunks (for recall@k); only the
                first ``top_k`` are used as context.
        """
        start = time.perf_counter()
        rewrite, timings = None, {}
        search_text = standalone = question
        codes: tuple[str, ...] = ()
        if self._rewriter is not None:
            rewrite, timings = self.rewrite(question, history)
            search_text = rewrite.rewrite.search_query
            standalone = rewrite.rewrite.standalone_question
            codes = tuple(rewrite.rewrite.product_codes)
        regions = self.regions_for(user)
        retrieval = self.retrieve_ranked(
            search_text,
            max(self.stage.top_k, retrieve_k or 0),
            rerank_query=standalone,
            extra_codes=codes,
            regions=regions,
        )
        context = retrieval.ranked[: self.stage.top_k]
        with timed_calls(self._generation_model) as gen:
            text = self._generator.generate(standalone, context)
        return Answer(
            text=text,
            context=context,
            retrieved=retrieval.ranked,
            timings_ms={
                **timings,
                **retrieval.timings_ms,
                "generate": gen.net_ms,  # excludes throttle and retry waits
                "generate_wait": gen.wait_ms,
                "generate_requests": float(gen.requests),  # 0 = answered from the cache
                "total": _ms_since(start),  # wall clock, waits included
            },
            candidates=retrieval.candidates,
            rewrite=rewrite,
            region_filter=regions,
        )


def make_rewriter(settings: Settings) -> QueryRewriter:
    """The query rewriter on the small model, with the manifest's product catalogue."""
    prompts = load_prompts()
    manifest = settings.paths.resolve(settings.paths.generated_dir) / "manifest.json"
    return QueryRewriter(
        json_completer(make_chat_model(settings, settings.llm.small_model)),
        prompts["rewrite_query"],
        prompts["rewrite_retry"],
        Catalogue(tuple(load_catalogue(manifest))),
        settings.query.history_turns,
        settings.query.max_attempts,
    )

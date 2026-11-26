"""RAGAS metrics on stored answers and contexts (Session 13 Part B).

Four metrics per golden row, from the stored answer and its rebuilt context (no new
answers):

- **faithfulness** (LLM): the share of the answer's statements the context supports;
- **answer relevancy** (LLM + embeddings): how well the answer addresses the question.
  One question is generated per answer (``strictness=1``, not RAGAS's default 3) to keep
  the Groq quota in check;
- **context precision** and **context recall** (no LLM): RAGAS's non-LLM variants. The
  retrieved context is compared with the *reference contexts*, the chunks of the item's
  expected documents, by string similarity.

The LLM is ``llm.ragas_model`` (Groq ``qwen/qwen3.8-27b``, thinking off: its own daily
quota, separate from the judge's) through our cached, throttled client. RAGAS calls
models asynchronously, which would bypass the throttle, so ``SyncGroqLLM`` sends every
call through the synchronous client. Embeddings are FastEmbed (``FastEmbedEmbeddings``).
RAGAS falls back to OpenAI models only when none is given; we always give both, and a
test fails if an OpenAI client is ever created.

Rows without a context (an agent answer without a knowledge call) get no context
metrics; rows without expected documents get no context precision or recall.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass

import groq
import httpx
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel
from ragas.dataset_schema import SingleTurnSample
from ragas.embeddings.base import LangchainEmbeddingsWrapper
from ragas.llms.base import LangchainLLMWrapper
from ragas.metrics import (
    Faithfulness,
    NonLLMContextPrecisionWithReference,
    NonLLMContextRecall,
    ResponseRelevancy,
)
from ragas.run_config import RunConfig

from rag_assistant.llm import RateLimitExhaustedError  # noqa: E402

METRICS = ("faithfulness", "answer_relevancy", "context_precision", "context_recall")


class SyncGroqLLM(LangchainLLMWrapper):
    """RAGAS's LangChain wrapper, but every call goes through the synchronous client, so
    our throttle, retries and cache apply (the async client would bypass them)."""

    async def agenerate_text(self, *args: object, **kwargs: object) -> object:  # type: ignore[override]
        """Run the synchronous generation in a thread."""
        return await asyncio.to_thread(self.generate_text, *args, **kwargs)  # type: ignore[arg-type]


class FastEmbedEmbeddings(Embeddings):
    """The project's FastEmbed embedder as LangChain embeddings."""

    def __init__(self, embedder: object) -> None:
        """``embedder`` is a ``rag_assistant.embeddings.Embedder``."""
        self._embedder = embedder

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """One vector per text."""
        return [list(map(float, self._embedder.embed_one(t))) for t in texts]  # type: ignore[attr-defined]

    def embed_query(self, text: str) -> list[float]:
        """The query's vector."""
        return list(map(float, self._embedder.embed_one(text)))  # type: ignore[attr-defined]


@dataclass(frozen=True)
class RagasRow:
    """One golden row's scores (None: the metric doesn't apply to this row)."""

    item_id: str
    model: str
    failure_tag: str
    faithfulness: float | None
    answer_relevancy: float | None
    context_precision: float | None
    context_recall: float | None

    def to_dict(self) -> dict:
        """JSON-ready."""
        return asdict(self)


@dataclass(frozen=True)
class Scorers:
    """The four metric objects, built once, and the model they score with."""

    model: str
    faithfulness: Faithfulness
    answer_relevancy: ResponseRelevancy
    context_precision: NonLLMContextPrecisionWithReference
    context_recall: NonLLMContextRecall


def make_scorers(llm: BaseChatModel, embeddings: Embeddings) -> Scorers:
    """The metrics with our LLM and embeddings (never RAGAS's OpenAI defaults)."""
    # Our Groq client already throttles and retries rate limits; RAGAS's own retry loop
    # (10 tries, up to a minute apart) would only delay a final error.
    run_config = RunConfig(max_retries=1, max_wait=1)
    ragas_llm = SyncGroqLLM(llm, run_config=run_config)
    ragas_embeddings = LangchainEmbeddingsWrapper(embeddings, run_config=run_config)
    return Scorers(
        model=str(getattr(llm, "model_name", None) or type(llm).__name__),
        faithfulness=Faithfulness(llm=ragas_llm),
        answer_relevancy=ResponseRelevancy(
            llm=ragas_llm, embeddings=ragas_embeddings, strictness=1
        ),
        context_precision=NonLLMContextPrecisionWithReference(),
        context_recall=NonLLMContextRecall(),
    )


def _number(value: object) -> float | None:
    """A finite float, or None (RAGAS returns NaN when it can't score)."""
    if value is None:
        return None
    number = float(value)  # type: ignore[arg-type]
    return None if math.isnan(number) else round(number, 4)


def _is_service_error(exc: BaseException) -> bool:
    """A quota, connection or API error anywhere in the chain: the run must stop (and
    resume later), never record the row as unscorable."""
    seen: BaseException | None = exc
    while seen is not None:
        if isinstance(seen, (RateLimitExhaustedError, groq.APIError, httpx.HTTPError)):
            return True
        seen = seen.__cause__ or seen.__context__
    return False


async def _score(metric: object, sample: SingleTurnSample) -> float | None:
    """One metric on one sample; None if the model's reply can't be scored. A service
    error (quota, connection) is re-raised, so the row isn't written and the run resumes."""
    try:
        return _number(await metric.single_turn_ascore(sample))  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001 - an unscorable reply is None, not a crash
        if _is_service_error(exc):
            raise
        return None


def score_row(
    scorers: Scorers,
    item_id: str,
    failure_tag: str,
    question: str,
    answer: str,
    contexts: Sequence[str],
    reference_contexts: Sequence[str],
) -> RagasRow:
    """The four metrics for one stored answer (context metrics only where they apply)."""
    sample = SingleTurnSample(
        user_input=question,
        response=answer,
        retrieved_contexts=list(contexts) or None,
        reference_contexts=list(reference_contexts) or None,
    )

    async def run() -> RagasRow:
        has_context = bool(contexts)
        has_reference = has_context and bool(reference_contexts)
        return RagasRow(
            item_id=item_id,
            model=scorers.model,
            failure_tag=failure_tag,
            faithfulness=await _score(scorers.faithfulness, sample) if has_context else None,
            answer_relevancy=await _score(scorers.answer_relevancy, sample),
            context_precision=(
                await _score(scorers.context_precision, sample) if has_reference else None
            ),
            context_recall=await _score(scorers.context_recall, sample) if has_reference else None,
        )

    return asyncio.run(run())


def mean(values: Sequence[float | None]) -> float | None:
    """The mean of the scored values (None if none was scored)."""
    scored = [v for v in values if v is not None]
    return round(sum(scored) / len(scored), 3) if scored else None


def summarise(rows: Sequence[RagasRow]) -> dict[str, dict[str, float | None]]:
    """Mean of each metric, overall (``"all"``) and per failure tag."""
    tags = ["all", *sorted({r.failure_tag for r in rows})]
    return {
        tag: {
            m: mean([getattr(r, m) for r in rows if tag == "all" or r.failure_tag == tag])
            for m in METRICS
        }
        for tag in tags
    }

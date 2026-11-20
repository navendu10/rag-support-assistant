"""The agent's knowledge tool behind the scenes: the v7 pipeline and its retrieval context.

``make_knowledge`` turns a v7 ``Pipeline`` into the function ``search_knowledge_base``
calls for one user. Each call also appends a ``RetrievalContext`` (what the evaluation
needs: retrieved, context and candidate chunks, the rewrite, the region filter and the
security events) to a list the caller keeps.

Session 12 (v10) runs the knowledge tool inside the MCP server. The server sends the
``RetrievalContext`` back as JSON in the tool's structured result (``to_dict``), and the
agent rebuilds it (``from_dict``), so v10 rows get the same recall and leak metrics as v9.
The chunk texts are the ones the pipeline used: from the sanitised collections, as always.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, field

from rag_assistant.agent.tools import KnowledgeResult
from rag_assistant.pipeline import Answer, Pipeline
from rag_assistant.query.rewrite import Rewrite, RewriteResult
from rag_assistant.retrieval.vector import RetrievedChunk
from rag_assistant.schemas import User


@dataclass(frozen=True)
class RetrievalContext:
    """The retrieval side of one knowledge call (the fields the evaluation reads)."""

    context: list[RetrievedChunk] = field(default_factory=list)
    retrieved: list[RetrievedChunk] = field(default_factory=list)
    candidates: list[RetrievedChunk] = field(default_factory=list)
    rewrite: RewriteResult | None = None
    region_filter: tuple[str, ...] | None = None
    security_events: int = 0
    cited: list[RetrievedChunk] = field(default_factory=list)  # the passages K1, K2...

    @classmethod
    def from_answer(cls, answer: Answer) -> RetrievalContext:
        """The retrieval context of a v7 pipeline answer."""
        return cls(
            context=list(answer.context),
            retrieved=list(answer.retrieved),
            candidates=list(answer.candidates),
            rewrite=answer.rewrite,
            region_filter=answer.region_filter,
            security_events=answer.security_events,
            cited=[answer.context[s.number - 1] for s in answer.sources],
        )

    def to_dict(self) -> dict:
        """JSON-ready (the MCP structured result)."""
        rewrite = None
        if self.rewrite is not None:
            rewrite = {**asdict(self.rewrite), "rewrite": self.rewrite.rewrite.model_dump()}
        return {
            "context": [asdict(c) for c in self.context],
            "retrieved": [asdict(c) for c in self.retrieved],
            "candidates": [asdict(c) for c in self.candidates],
            "rewrite": rewrite,
            "region_filter": None if self.region_filter is None else list(self.region_filter),
            "security_events": self.security_events,
            "cited": [asdict(c) for c in self.cited],
        }

    @classmethod
    def from_dict(cls, data: dict) -> RetrievalContext:
        """Rebuild from ``to_dict`` output."""
        chunks = {
            key: [RetrievedChunk(**c) for c in data.get(key, [])]
            for key in ("context", "retrieved", "candidates", "cited")
        }
        raw = data.get("rewrite")
        rewrite = None
        if raw is not None:  # stored as validated: rebuild without re-checking the catalogue
            rewrite = RewriteResult(
                rewrite=Rewrite.model_construct(**raw["rewrite"]),
                fallback=raw["fallback"],
                attempts=raw["attempts"],
                error=raw.get("error"),
            )
        region = data.get("region_filter")
        return cls(
            **chunks,
            rewrite=rewrite,
            region_filter=None if region is None else tuple(region),
            security_events=int(data.get("security_events", 0)),
        )


def make_knowledge(
    pipeline: Pipeline,
    user: User,
    sink: list[RetrievalContext],
    retrieve_k: int | None = None,
) -> Callable[[str], KnowledgeResult]:
    """The knowledge function for ``user``: the v7 answer, its cited passages, and (into
    ``sink``) its retrieval context."""

    def knowledge(question: str) -> KnowledgeResult:
        answer = pipeline.answer(question, user, retrieve_k=retrieve_k)
        context = RetrievalContext.from_answer(answer)
        sink.append(context)
        answered = answer.path is None or answer.path.handoff_reason is None
        return KnowledgeResult(answered, answer.model_text or "", context.cited)

    return knowledge

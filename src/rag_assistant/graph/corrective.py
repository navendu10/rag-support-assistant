"""Corrective RAG flow with human handoff (Session 8, v6), built with LangGraph.

::

    rewrite -> retrieve -> grade --pass--> generate -> check --ok--> answer
                             |                ^            |
                             |                |            +--fail, retry left--> retry
                             +--low, retry left--> retry   |    (then regenerate)
                                  (then grade, generate)   +--fail, retry spent--> handoff

- **One retry per question.** Two things can trigger it, and they share one budget:
  - ``low_score``: **grade** finds no reranked candidate with a cross-encoder score
    (``rerank_score``, sigmoid 0-1) of at least ``min_score``;
  - ``not_found``: **check** finds the grounded answer not found in the sources, or its
    output or citations invalid.
  Once the retry is spent, a failed check hands off. The flow can't loop.
- **retry** is one wider search, the same for both triggers: the customer's **original
  message** plus the catalogue names and codes of the products the rewriter resolved
  (added by code from the catalogue, never rewriter free text), ``retry_fetch_k``
  candidates, and ``retry_top_k`` chunks of context. The reranker and the regenerated
  answer still use the standalone question, so "it" stays resolved. The retry calls the
  same ``retrieve_ranked`` with the same ``user`` and ``regions``, so it uses the same
  Weaviate connection, key, collections and region filter: it can never widen access.
  - After ``low_score`` it keeps whichever retrieval scores higher, grades again and
    generates (a second low grade still generates).
  - After ``not_found`` it always uses the wider retrieval and regenerates.
- The score is never a reason to hand off on its own: handing off on it would refuse
  answerable questions whose best chunk scores low (7 of 39 in v3 below 0.01).

The nodes are small functions over ``FlowState``; the steps that touch services
(rewriting, retrieval, generation, the catalogue) come from ``FlowSteps``, which the
pipeline provides and unit tests fake.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol, TypedDict

from langgraph.graph import END, START, StateGraph

from rag_assistant.generation.grounded import GroundedResult
from rag_assistant.graph.handoff import TicketStore, handoff_reason, handoff_text, new_ticket
from rag_assistant.query.rewrite import RewriteResult
from rag_assistant.retrieval.result import Retrieval
from rag_assistant.schemas import Product, Turn, User

Grade = Literal["pass", "low"]
RetryTrigger = Literal["low_score", "not_found", "none"]


class FlowSteps(Protocol):
    """The service-backed steps the flow needs (``Pipeline`` implements them)."""

    has_rewriter: bool

    def rewrite(
        self, question: str, history: Sequence[Turn] = ()
    ) -> tuple[RewriteResult, dict[str, float]]:
        """Rewrite the latest message with the history."""

    def regions_for(self, user: User | None) -> tuple[str, ...] | None:
        """The region filter values from the user's profile."""

    def products_for(self, codes: Sequence[str]) -> list[Product]:
        """Catalogue entries for product codes (unknown codes are skipped)."""

    def retrieve_ranked(
        self,
        question: str,
        k: int,
        *,
        rerank_query: str | None = None,
        extra_codes: Sequence[str] = (),
        regions: tuple[str, ...] | None = None,
        user: User | None = None,
        fetch_k: int | None = None,
    ) -> Retrieval:
        """Search, rerank and cut to ``k``."""

    def generate_grounded(
        self, question: str, chunks: Sequence, user: User
    ) -> tuple[GroundedResult, dict[str, float]]:
        """The grounded answer and its timings."""


class FlowState(TypedDict, total=False):
    """Everything the nodes read and write."""

    question: str
    user: User
    history: Sequence[Turn]
    retrieve_k: int
    rewrite: RewriteResult | None
    search_text: str
    standalone: str
    codes: tuple[str, ...]
    regions: tuple[str, ...] | None
    retrieval: Retrieval
    context_k: int
    best_score: float | None
    grade: Grade
    retry_used: bool
    retry_trigger: RetryTrigger
    retry_best_score: float | None
    retry_kept: bool
    retry_changed_outcome: bool | None
    grounded: GroundedResult
    handoff_reason: str | None
    ticket_id: str | None
    text: str
    model_text: str | None
    timings: dict[str, float]


@dataclass(frozen=True)
class FlowPath:
    """The route one question took (stored in each results.jsonl row).

    ``retry_changed_outcome``: after a ``not_found`` retry, True if the regenerated answer
    passed the check (a handoff turned into an answer); None without a retry and after a
    ``low_score`` retry, where there is no first answer to compare with.
    """

    grade: Grade
    best_score: float | None
    retry_used: bool
    retry_best_score: float | None = None
    retry_kept: bool = False
    handoff_reason: str | None = None
    ticket_id: str | None = None
    retry_trigger: RetryTrigger = "none"
    retry_changed_outcome: bool | None = None


@dataclass(frozen=True)
class FlowOutcome:
    """The flow's final state, in the shape the pipeline turns into an ``Answer``."""

    text: str
    model_text: str | None
    grounded: GroundedResult
    retrieval: Retrieval
    rewrite: RewriteResult | None
    regions: tuple[str, ...] | None
    path: FlowPath
    context_k: int
    timings: dict[str, float] = field(default_factory=dict)


def grade_score(best: float | None, min_score: float) -> Grade:
    """``pass`` if the best rerank score reaches ``min_score``; ``low`` otherwise or if none."""
    return "pass" if best is not None and best >= min_score else "low"


def retry_query(message: str, products: Sequence[Product]) -> str:
    """The retry's search text: the original message plus each resolved product's
    catalogue name and code (from the catalogue, not from rewriter text)."""
    return " ".join([message, *(f"{p.name} {p.code}" for p in products)])


class CorrectiveFlow:
    """The v6 graph: rewrite, retrieve, grade, generate, check, with at most one retry,
    then answer or hand off."""

    def __init__(
        self,
        steps: FlowSteps,
        tickets: TicketStore,
        prompts: dict[str, str],
        min_score: float,
        retry_fetch_k: int,
        retry_top_k: int,
        top_k: int,
    ) -> None:
        """Build and compile the graph over ``steps``."""
        self._steps = steps
        self._tickets = tickets
        self._prompts = prompts
        self._min_score = min_score
        self._retry_fetch_k = retry_fetch_k
        self._retry_top_k = retry_top_k
        self._top_k = top_k
        self.graph = self._build().compile()

    def _build(self) -> StateGraph:
        """Nodes and edges, as in the module docstring."""
        g = StateGraph(FlowState)
        g.add_node("rewrite", self.rewrite)
        g.add_node("retrieve", self.retrieve)
        g.add_node("grade", self.grade)
        g.add_node("retry", self.retry)
        g.add_node("generate", self.generate)
        g.add_node("answer", self.answer)
        g.add_node("handoff", self.handoff)
        g.add_edge(START, "rewrite")
        g.add_edge("rewrite", "retrieve")
        g.add_edge("retrieve", "grade")
        g.add_conditional_edges("grade", self.after_grade, ["retry", "generate"])
        g.add_conditional_edges("retry", self.after_retry, ["grade", "generate"])
        g.add_conditional_edges("generate", self.check, ["answer", "retry", "handoff"])
        g.add_edge("answer", END)
        g.add_edge("handoff", END)
        return g

    def run(
        self, question: str, user: User, history: Sequence[Turn] = (), retrieve_k: int = 0
    ) -> FlowOutcome:
        """Answer ``question`` for ``user``; returns the final state as a ``FlowOutcome``."""
        s = self.graph.invoke(
            {
                "question": question,
                "user": user,
                "history": history,
                "retrieve_k": max(self._top_k, retrieve_k),
                "context_k": self._top_k,
                "retry_used": False,
                "retry_trigger": "none",
                "timings": {},
            }
        )
        path = FlowPath(
            grade=s["grade"],
            best_score=s["best_score"],
            retry_used=s["retry_used"],
            retry_best_score=s.get("retry_best_score"),
            retry_kept=s.get("retry_kept", False),
            handoff_reason=s.get("handoff_reason"),
            ticket_id=s.get("ticket_id"),
            retry_trigger=s["retry_trigger"],
            retry_changed_outcome=s.get("retry_changed_outcome"),
        )
        return FlowOutcome(
            text=s["text"],
            model_text=s["model_text"],
            grounded=s["grounded"],
            retrieval=s["retrieval"],
            rewrite=s["rewrite"],
            regions=s["regions"],
            path=path,
            context_k=s["context_k"],
            timings=s["timings"],
        )

    # ------------------------------------------------------------------ nodes

    def rewrite(self, state: FlowState) -> FlowState:
        """Rewrite the message (if the stage rewrites); read the region from the profile."""
        question = state["question"]
        update: FlowState = {
            "rewrite": None,
            "search_text": question,
            "standalone": question,
            "codes": (),
            "regions": self._steps.regions_for(state["user"]),
        }
        if self._steps.has_rewriter:
            result, timings = self._steps.rewrite(question, state["history"])
            update |= {
                "rewrite": result,
                "search_text": result.rewrite.search_query,
                "standalone": result.rewrite.standalone_question,
                "codes": tuple(result.rewrite.product_codes),
                "timings": {**state["timings"], **timings},
            }
        return update

    def _search(self, state: FlowState, text: str, k: int, fetch_k: int | None) -> Retrieval:
        """One search + rerank, always with the state's user, regions and codes."""
        return self._steps.retrieve_ranked(
            text,
            k,
            rerank_query=state["standalone"],
            extra_codes=state["codes"],
            regions=state["regions"],
            user=state["user"],
            fetch_k=fetch_k,
        )

    def retrieve(self, state: FlowState) -> FlowState:
        """The normal search: the rewriter's search query, ``fetch_k`` candidates."""
        retrieval = self._search(state, state["search_text"], state["retrieve_k"], None)
        return {"retrieval": retrieval, "timings": {**state["timings"], **retrieval.timings_ms}}

    def grade(self, state: FlowState) -> FlowState:
        """Grade the current retrieval by its best rerank score.

        ``best_score`` keeps the first search's score; the retry's is ``retry_best_score``.
        """
        best = state["retrieval"].best_rerank_score
        update: FlowState = {"grade": grade_score(best, self._min_score)}
        if not state["retry_used"]:
            update["best_score"] = best
        return update

    def after_grade(self, state: FlowState) -> str:
        """A low grade uses the retry if it is still available; otherwise generate."""
        return "retry" if state["grade"] == "low" and not state["retry_used"] else "generate"

    def retry(self, state: FlowState) -> FlowState:
        """The one wider search: original message + resolved products, wider fetch and context.

        Same user, regions and codes as the first search (``_search``), so the same
        connection, collections and filter. The trigger is ``not_found`` if an answer was
        already generated, ``low_score`` otherwise.
        """
        trigger: RetryTrigger = "not_found" if "grounded" in state else "low_score"
        first = state["retrieval"]
        text = retry_query(state["question"], self._steps.products_for(state["codes"]))
        k = max(self._retry_top_k, state["retrieve_k"])
        second = self._search(state, text, k, self._retry_fetch_k)
        retry_best = second.best_rerank_score
        if trigger == "not_found":
            kept = True
        else:
            kept = retry_best is not None and (
                first.best_rerank_score is None or retry_best > first.best_rerank_score
            )
        timings = {f"retry_{step}": ms for step, ms in second.timings_ms.items()}
        return {
            "retrieval": second if kept else first,
            "context_k": self._retry_top_k if kept else state["context_k"],
            "retry_used": True,
            "retry_trigger": trigger,
            "retry_best_score": retry_best,
            "retry_kept": kept,
            "timings": {**state["timings"], **timings},
        }

    def after_retry(self, state: FlowState) -> str:
        """After a low-score retry, grade again; after a not-found retry, regenerate."""
        return "grade" if state["retry_trigger"] == "low_score" else "generate"

    def generate(self, state: FlowState) -> FlowState:
        """The grounded answer from the current context (``top_k``, or ``retry_top_k``)."""
        context = state["retrieval"].ranked[: state["context_k"]]
        result, timings = self._steps.generate_grounded(state["standalone"], context, state["user"])
        if state["retry_trigger"] == "not_found":  # the second generation: regenerate_*
            timings = {k.replace("generate", "regenerate"): v for k, v in timings.items()}
        return {"grounded": result, "timings": {**state["timings"], **timings}}

    def check(self, state: FlowState) -> str:
        """Answer if found and valid; else retry once (budget left) or hand off."""
        if handoff_reason(state["grounded"]) is None:
            return "answer"
        return "handoff" if state["retry_used"] else "retry"

    def answer(self, state: FlowState) -> FlowState:
        """The grounded answer as it is."""
        text = state["grounded"].answer
        changed = True if state["retry_trigger"] == "not_found" else None
        return {
            "text": text,
            "model_text": text,
            "handoff_reason": None,
            "ticket_id": None,
            "retry_changed_outcome": changed,
        }

    def handoff(self, state: FlowState) -> FlowState:
        """Customers: polite message and a ticket. Agents: not found, closest sources."""
        user, result = state["user"], state["grounded"]
        context = state["retrieval"].ranked[: state["context_k"]]
        reason = handoff_reason(result) or "not_found"
        text, model_text = handoff_text(user, result, context, self._prompts)
        ticket_id = None
        if user.role == "customer":
            ticket = new_ticket(user, state["question"], state["standalone"], reason, context)
            self._tickets.add(ticket)
            ticket_id = ticket.ticket_id
        return {
            "text": text,
            "model_text": model_text,
            "handoff_reason": reason,
            "ticket_id": ticket_id,
            "retry_changed_outcome": False if state["retry_trigger"] == "not_found" else None,
        }

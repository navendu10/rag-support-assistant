"""The v8 tool-calling agent (Session 10), a small LangGraph graph.

::

    model --tool calls--> tools --budget left--> model
      |                     |
      |                     +--tool limit reached--> finalize (one answer, no tools)
      +--final answer--> guard --pass--> END
                           |
                           +--fail, guard budget and cap left--> regenerate --> guard
                           +--fail, nothing left--> fallback (fixed message) --> END

- **model** is the large Groq model with the tools bound and a role-aware system prompt
  (customer or agent, from the profile). Its tool calls run in **tools**, bound to the
  signed-in user's ``AgentSession`` (``agent/tools.py``): the model never supplies a user.
- **The step limit** (``max_tool_calls``, 6 in v8) counts tool calls. When it is reached,
  **finalize** asks for one answer with the tools removed (``hit_step_limit``).
- **Answer generations are capped** (``injection.max_generations``, 3) across the first
  answer, the finalize call and the guard's regeneration, as in v7.
- **guard** is v7's output guard in agent mode. Allowed sources are the passages
  ``search_knowledge_base`` returned this turn (their text, not its answer) and this turn's
  order-tool results, whose figures and dates our code computed. Seller attribution and
  the VoltCart-policy rule apply unchanged; dates are checked too. An answer that claims a
  ticket fails unless ``draft_ticket`` really drafted one this turn.
- **fallback** is a fixed reply that claims nothing.

``AgentPipeline`` gives the agent the same ``answer()`` shape as ``Pipeline``, so
``evaluate.py``, ``chat.py`` and ``redteam.py`` run v8 unchanged. Its knowledge tool is the
stage's own v7 pipeline, which writes no tickets in v8 (``handoff_writes_ticket: false``).
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from types import TracebackType
from typing import Literal, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.tools import StructuredTool
from langgraph.graph import END, START, StateGraph

from rag_assistant.agent.orders import OrderStore
from rag_assistant.agent.rules import BusinessRules, load_business_rules
from rag_assistant.agent.tools import AgentSession, KnowledgeResult, ToolCall, make_tools
from rag_assistant.config import Settings, StageConfig, get_settings, load_prompts, load_stage
from rag_assistant.graph.handoff import MemoryTicketStore, TicketStore
from rag_assistant.llm import make_chat_model
from rag_assistant.pipeline import Answer, Pipeline, make_guard
from rag_assistant.schemas import Product, Turn, User, load_catalogue
from rag_assistant.security.output_guard import OutputGuard

Outcome = Literal["answered", "fallback"]
KNOWLEDGE_TOOL = "search_knowledge_base"
DRAFT_TOOL = "draft_ticket"
# A sentence that says a ticket was (or is being, or will be) created: "I'll create a
# draft repair ticket", "I've raised a ticket", "a ticket has been opened", or a
# "Draft repair ticket" heading. An offer ("I can draft a ticket") is not a claim.
_TICKET_CLAIM = re.compile(
    r"\b(?:i['\u2019]ll|i will|i['\u2019]ve|i have|i['\u2019]m|i am|we['\u2019]ve|"
    r"we have|has been|have been|is being|was)\s+(?:\w+\s+){0,2}?"
    r"(?:creat|draft|rais|open|log|submit|fil)\w*\b[^.\n]{0,60}\bticket"
    r"|\bdraft\s+(?:repair|return)\s+ticket\b"
    r"|\bticket\s+(?:has|have|was|is)\s+(?:been\s+|now\s+)?(?:creat|draft|rais|open|log|submit|fil)\w*",
    re.IGNORECASE,
)


def claimed_ticket_problems(answer: str, calls: Sequence[ToolCall]) -> list[str]:
    """An answer that claims a ticket exists must have drafted one with ``draft_ticket``.

    The v7 grounding rule "never claim to have taken an action" applies to the agent too;
    this is its deterministic check (Session 10: the TV case claimed a draft it never made).
    """
    drafted = any(c.name == DRAFT_TOOL and c.result.get("status") == "draft" for c in calls)
    match = _TICKET_CLAIM.search(answer)
    if drafted or match is None:
        return []
    return [f'claims a ticket ("{match.group(0)}"), but draft_ticket drafted none this turn']


class AgentState(TypedDict):
    """What the nodes read and write."""

    messages: list[BaseMessage]
    tool_calls_made: int
    generations: int
    model_calls: int
    final: str
    hit_step_limit: bool
    guard_used: bool
    guard_passed: bool
    guard_problems: list[str]
    outcome: Outcome


@dataclass(frozen=True)
class AgentResult:
    """One agent turn: the reply, every tool call, and how it got there."""

    text: str
    calls: list[ToolCall]
    passages: list
    tool_calls: int
    model_calls: int
    generations: int
    hit_step_limit: bool
    guard_problems: list[str]
    outcome: Outcome
    ms: float
    timings: dict[str, float] = field(default_factory=dict)

    @property
    def tools_used(self) -> list[str]:
        """Tool names in call order."""
        return [c.name for c in self.calls]


def _history(turns: Sequence[Turn]) -> list[BaseMessage]:
    """Earlier turns as chat messages."""
    return [HumanMessage(t.content) if t.role == "user" else AIMessage(t.content) for t in turns]


def _text(message: BaseMessage) -> str:
    """A message's text content."""
    return message.content if isinstance(message.content, str) else str(message.content)


class AgentRunner:
    """Runs the agent graph for one session and question."""

    def __init__(
        self,
        model: BaseChatModel,
        prompts: dict[str, str],
        guard: OutputGuard | None,
        max_tool_calls: int,
        max_generations: int = 3,
    ) -> None:
        """``model`` must support ``bind_tools``; the limits come from the stage and config."""
        if max_tool_calls < 1 or max_generations < 1:
            raise ValueError("max_tool_calls and max_generations must be at least 1")
        self._model = model
        self._prompts = prompts
        self._guard = guard
        self._max_tool_calls = max_tool_calls
        self._max_generations = max_generations

    # --------------------------------------------------------------- graph

    def _build(self, session: AgentSession, tools: list[StructuredTool]) -> StateGraph:
        """The graph for one session (the tools are bound to it)."""
        by_name = {t.name: t for t in tools}
        with_tools = self._model.bind_tools(tools)

        def model(state: AgentState) -> dict:
            reply = with_tools.invoke(state["messages"])
            update: dict = {"model_calls": state["model_calls"] + 1}
            remaining = self._max_tool_calls - state["tool_calls_made"]
            if getattr(reply, "tool_calls", None):
                calls = reply.tool_calls[:remaining]  # never more than the limit allows
                kept = AIMessage(content=reply.content, tool_calls=calls)
                return {**update, "messages": [*state["messages"], kept]}
            return {
                **update,
                "messages": [*state["messages"], reply],
                "final": _text(reply).strip(),
                "generations": state["generations"] + 1,
            }

        def after_model(state: AgentState) -> str:
            last = state["messages"][-1]
            return "tools" if isinstance(last, AIMessage) and last.tool_calls else "guard"

        def run_tools(state: AgentState) -> dict:
            last = state["messages"][-1]
            assert isinstance(last, AIMessage)
            replies = []
            for call in last.tool_calls:
                tool = by_name.get(call["name"])
                try:
                    if tool is None:
                        raise ValueError(f"unknown tool {call['name']}")
                    content = tool.invoke(call["args"])
                except Exception as exc:  # noqa: BLE001 - the model sees the error, not a crash
                    content = json.dumps({"status": "error", "message": str(exc)[:300]})
                replies.append(ToolMessage(content=content, tool_call_id=call["id"]))
            return {
                "messages": [*state["messages"], *replies],
                "tool_calls_made": state["tool_calls_made"] + len(last.tool_calls),
            }

        def after_tools(state: AgentState) -> str:
            return "finalize" if state["tool_calls_made"] >= self._max_tool_calls else "model"

        def finalize(state: AgentState) -> dict:
            update: dict = {"hit_step_limit": True}
            if state["generations"] >= self._max_generations:
                return {**update, "final": ""}
            note = HumanMessage(self._prompts["agent_finalize_note"])
            reply = self._model.invoke([*state["messages"], note])
            return {
                **update,
                "messages": [*state["messages"], note, reply],
                "final": _text(reply).strip(),
                "generations": state["generations"] + 1,
                "model_calls": state["model_calls"] + 1,
            }

        def guard(state: AgentState) -> dict:
            if not state["final"]:
                return {"guard_passed": False}
            if self._guard is None:
                return {"guard_passed": True}
            verdict = self._guard.check(
                state["final"],
                list(range(1, len(session.passages) + 1)),
                session.passages,
                [json.dumps(c.result) for c in session.calls if c.name != KNOWLEDGE_TOOL],
                check_dates=True,
            )
            problems = verdict.problems + claimed_ticket_problems(state["final"], session.calls)
            return {
                "guard_passed": not problems,
                "guard_problems": [*state["guard_problems"], *problems],
            }

        def after_guard(state: AgentState) -> str:
            if state["guard_passed"]:
                return END
            can_retry = not state["guard_used"] and state["generations"] < self._max_generations
            return "regenerate" if can_retry and state["final"] else "fallback"

        def regenerate(state: AgentState) -> dict:
            problems = "; ".join(state["guard_problems"][-3:])
            note = HumanMessage(self._prompts["agent_guard_note"].format(problems=problems))
            reply = self._model.invoke([*state["messages"], note])
            return {
                "messages": [*state["messages"], note, reply],
                "final": _text(reply).strip(),
                "generations": state["generations"] + 1,
                "model_calls": state["model_calls"] + 1,
                "guard_used": True,
            }

        def fallback(state: AgentState) -> dict:
            key = (
                "agent_fallback_agent"
                if session.user.role == "agent"
                else "agent_fallback_customer"
            )
            return {"final": self._prompts[key], "outcome": "fallback"}

        g = StateGraph(AgentState)
        for name, node in (
            ("model", model),
            ("tools", run_tools),
            ("finalize", finalize),
            ("guard", guard),
            ("regenerate", regenerate),
            ("fallback", fallback),
        ):
            g.add_node(name, node)
        g.add_edge(START, "model")
        g.add_conditional_edges("model", after_model, ["tools", "guard"])
        g.add_conditional_edges("tools", after_tools, ["model", "finalize"])
        g.add_edge("finalize", "guard")
        g.add_conditional_edges("guard", after_guard, ["regenerate", "fallback", END])
        g.add_edge("regenerate", "guard")
        g.add_edge("fallback", END)
        return g

    def run(
        self, session: AgentSession, question: str, history: Sequence[Turn] = ()
    ) -> AgentResult:
        """Answer ``question`` for ``session.user``; returns the reply and the trace."""
        start = time.perf_counter()
        key = "agent_system_agent" if session.user.role == "agent" else "agent_system_customer"
        system = SystemMessage(self._prompts[key].format(today=session.today.isoformat()))
        graph = self._build(session, make_tools(session)).compile()
        state = graph.invoke(
            {
                "messages": [system, *_history(history), HumanMessage(question)],
                "tool_calls_made": 0,
                "generations": 0,
                "model_calls": 0,
                "final": "",
                "hit_step_limit": False,
                "guard_used": False,
                "guard_passed": False,
                "guard_problems": [],
                "outcome": "answered",
            },
            {"recursion_limit": 4 * (self._max_tool_calls + self._max_generations) + 10},
        )
        return AgentResult(
            text=state["final"],
            calls=list(session.calls),
            passages=list(session.passages),
            tool_calls=state["tool_calls_made"],
            model_calls=state["model_calls"],
            generations=state["generations"],
            hit_step_limit=state["hit_step_limit"],
            guard_problems=state["guard_problems"],
            outcome=state["outcome"],
            ms=round((time.perf_counter() - start) * 1000, 1),
        )


# ------------------------------------------------------------- the pipeline face


class AgentPipeline:
    """v8 with ``Pipeline``'s interface: ``answer()`` runs the agent for the user.

    Its knowledge tool is the stage's own v7 pipeline; the last knowledge call's
    retrieval fills the ``Answer``'s context fields, so the golden-set metrics still work.
    """

    def __init__(
        self,
        stage: StageConfig,
        knowledge: Pipeline,
        runner: AgentRunner,
        orders: OrderStore,
        rules: BusinessRules,
        catalogue: dict[str, Product],
    ) -> None:
        """Assemble from built parts (``from_stage`` builds them from config)."""
        if not stage.agent:
            raise ValueError(f"stage {stage.name} has agent: false")
        self.stage = stage
        self._knowledge = knowledge
        self._runner = runner
        self._orders = orders
        self._rules = rules
        self._catalogue = catalogue

    @classmethod
    def from_stage(
        cls,
        stage: str | StageConfig,
        settings: Settings | None = None,
        tickets: TicketStore | None = None,
        allow_redteam: bool = False,
    ) -> AgentPipeline:
        """Build v8 from config: the v7 knowledge pipeline, the order store and the model."""
        settings = settings or get_settings()
        stage = load_stage(stage) if isinstance(stage, str) else stage
        knowledge = Pipeline.from_stage(
            stage, settings, tickets or MemoryTicketStore(), allow_redteam=allow_redteam
        )
        guard, max_generations = make_guard(stage, settings)
        runner = AgentRunner(
            make_chat_model(settings, settings.llm.large_model),
            load_prompts(),
            guard,
            int(stage.max_tool_calls or 1),
            max_generations,
        )
        generated = settings.paths.resolve(settings.paths.generated_dir)
        catalogue = {p.code: p for p in load_catalogue(generated / "manifest.json")}
        orders = OrderStore(generated / "orders.db")
        return cls(stage, knowledge, runner, orders, load_business_rules(), catalogue)

    def __enter__(self) -> AgentPipeline:
        """Enter the context manager."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close the knowledge pipeline and the database."""
        self.close()

    def close(self) -> None:
        """Release the Weaviate clients and the database connection."""
        self._knowledge.close()
        self._orders.close()

    def answer(
        self,
        question: str,
        user: User | None = None,
        history: Sequence[Turn] = (),
        retrieve_k: int | None = None,
    ) -> Answer:
        """Run the agent for ``user`` and return its reply as an ``Answer``."""
        if user is None:
            raise ValueError("the agent needs a signed-in user")
        last: list[Answer] = []

        def knowledge(q: str) -> KnowledgeResult:
            answer = self._knowledge.answer(q, user, retrieve_k=retrieve_k)
            last.append(answer)
            answered = answer.path is None or answer.path.handoff_reason is None
            cited = [answer.context[s.number - 1] for s in answer.sources]
            return KnowledgeResult(answered, answer.model_text or "", cited)

        session = AgentSession(user, self._orders, self._rules, self._catalogue, knowledge)
        result = self._runner.run(session, question, history)
        kb = last[-1] if last else None
        return Answer(
            text=result.text,
            context=kb.context if kb else [],
            retrieved=kb.retrieved if kb else [],
            timings_ms={"total": result.ms, "agent_model_calls": float(result.model_calls)},
            candidates=kb.candidates if kb else [],
            rewrite=kb.rewrite if kb else None,
            region_filter=kb.region_filter if kb else None,
            security_events=sum(a.security_events for a in last),
            model_text=None if result.outcome == "fallback" else result.text,
            agent=result,
        )


def open_pipeline(
    stage: StageConfig,
    settings: Settings,
    tickets: TicketStore | None = None,
    allow_redteam: bool = False,
) -> Pipeline | AgentPipeline:
    """The stage's pipeline: the agent for ``agent`` stages, else the v0-v7 pipeline."""
    if stage.agent:
        return AgentPipeline.from_stage(stage, settings, tickets, allow_redteam)
    return Pipeline.from_stage(stage, settings, tickets, allow_redteam=allow_redteam)

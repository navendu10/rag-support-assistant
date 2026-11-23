"""The tool-calling agent: v8 (Session 10) and v9 with human approval (Session 11).

::

    model --tool calls--> tools --budget left--> model
      |                     |
      |                     +--tool limit reached--> finalize (one answer, no tools)
      +--final answer--> guard --pass--> END            (v8, or v9 with no draft)
                           |      +--pass, a draft--> await_approval (v9: interrupt)
                           |                              | resumed by the client only
                           |                              +--approve--> submit --> END
                           |                              +--reject, superseded--> close_draft
                           +--fail, guard budget and cap left--> regenerate --> guard
                           +--fail, nothing left--> fallback (fixed message) --> END

- **model** is the large Groq model with the tools bound and a role-aware system prompt
  (customer or agent, from the profile). Its tool calls run in **tools**, bound to the
  signed-in user's ``AgentSession`` (``agent/tools.py``): the model never supplies a user.
- **The step limit** (``max_tool_calls``, 6) counts tool calls. When it is reached,
  **finalize** asks for one answer with the tools removed (``hit_step_limit``).
- **Answer generations are capped** (``injection.max_generations``, 3) across the first
  answer, the finalize call and the guard's regeneration, as in v7.
- **guard** is v7's output guard in agent mode. Allowed sources are the passages
  ``search_knowledge_base`` returned this turn (their text, not its answer) and this turn's
  order-tool results, whose figures and dates our code computed. A draft's free-text
  reason is never a source. Ticket claims are checked by code: in v8 a claimed ticket
  needs a real draft; in v9 ``approval_claim_problems`` also rejects any claim that a
  ticket was submitted or will be reviewed, and any "waiting for approval" without a draft.
- **await_approval** (v9) pauses the graph with a LangGraph ``interrupt`` whose payload is
  the draft from graph state, not the model's description of it. The state is saved by a
  checkpointer (SQLite in chat, memory in evaluation), one thread per conversation. Only
  ``AgentPipeline.approve``/``reject``/``supersede`` resume it, with a ``Command`` built by
  our code: conversation text never does.
- **submit** is our code (``agent/tickets.py``): it re-checks the hash, the owner, the order
  and its eligibility, and the daily limit, then writes the ticket. Its reply is a fixed
  template filled from the ticket record, so it can't overclaim. There is no submit tool.
- **fallback** is a fixed reply that claims nothing.

``AgentPipeline`` gives the agent the same ``answer()`` shape as ``Pipeline``, so
``evaluate.py``, ``chat.py`` and ``redteam.py`` run v8 and v9 unchanged.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field, replace
from types import TracebackType
from typing import Literal, TypedDict

import groq
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, interrupt

from rag_assistant.agent.audit import AuditLog, FileAudit, MemoryAudit
from rag_assistant.agent.knowledge import RetrievalContext, make_knowledge
from rag_assistant.agent.orders import OrderStore
from rag_assistant.agent.rules import BusinessRules, load_business_rules
from rag_assistant.agent.tickets import Draft, Submitter, TicketDB
from rag_assistant.agent.tools import AgentSession, KnowledgeResult, ToolCall, make_tools
from rag_assistant.config import Settings, StageConfig, get_settings, load_prompts, load_stage
from rag_assistant.graph.handoff import MemoryTicketStore, TicketStore
from rag_assistant.llm import make_chat_model
from rag_assistant.pipeline import Answer, Pipeline, make_guard
from rag_assistant.schemas import Product, Turn, User, load_catalogue
from rag_assistant.security.output_guard import OutputGuard

Outcome = Literal["answered", "fallback", "submitted", "existing", "refused", "closed"]
KNOWLEDGE_TOOL = "search_knowledge_base"
DRAFT_TOOL = "draft_ticket"
# A sentence that says a ticket was (or is being, or will be) created: "I'll create a
# draft repair ticket", "I've raised a ticket", "a ticket has been opened", or a
# "Draft repair ticket" heading. An offer ("I can draft a ticket") is not a claim.
_TICKET_CLAIM = re.compile(
    r"\b(?:i['’]ll|i will|i['’]ve|i have|i['’]m|i am|we['’]ve|"
    r"we have|has been|have been|is being|was)\s+(?:\w+\s+){0,2}?"
    r"(?:creat|draft|rais|open|log|submit|fil)\w*\b[^.\n]{0,60}\bticket"
    r"|\bdraft\s+(?:repair|return)\s+ticket\b"
    r"|\bticket\s+(?:has|have|was|is)\s+(?:been\s+|now\s+)?(?:creat|draft|rais|open|log|submit|fil)\w*",
    re.IGNORECASE,
)


def claimed_ticket_problems(answer: str, calls: Sequence[ToolCall]) -> list[str]:
    """v8: an answer that claims a ticket exists must have drafted one with ``draft_ticket``.

    The v7 grounding rule "never claim to have taken an action" applies to the agent too;
    this is its deterministic check (Session 10: the TV case claimed a draft it never made).
    """
    drafted = any(c.name == DRAFT_TOOL and c.result.get("status") == "draft" for c in calls)
    match = _TICKET_CLAIM.search(answer)
    if drafted or match is None:
        return []
    return [f'claims a ticket ("{match.group(0)}"), but draft_ticket drafted none this turn']


# ------------------------------------------------- v9: honest ticket wording

_ACTION = r"(?:submit|creat|draft|rais|open|log|fil|sen[dt]|pass|forward|escalat|regist)\w*"
_NOUN = r"(?:ticket|request|claim|case)s?"
# "I've submitted your ticket", "the request has been sent", "a draft return ticket"...
_ACTION_CLAIM = re.compile(
    r"\b(?:i['’]ll|i will|i['’]ve|i have|i['’]m|i am|we['’]ve|we have|"
    r"we will|we['’]ll|has been|have been|is being|was|were|is now|are now)\s+"
    rf"(?:\w+\s+){{0,3}}?{_ACTION}\b[^.\n]{{0,60}}?\b{_NOUN}\b"
    rf"|\b{_NOUN}\s+(?:\S+\s+){{0,2}}?(?:has|have|was|were|is|are)\s+(?:been\s+|now\s+)?"
    rf"{_ACTION}\b"
    r"|\bdraft\s+(?:repair|return|handoff)\s+ticket\b",
    re.IGNORECASE,
)
_SUBMIT_WORD = re.compile(r"submit|sen[dt]\b|forward|escalat|regist|logged|filed", re.IGNORECASE)
# "a support agent will review it", "our team will get back to you", "you'll hear back".
_REVIEW_CLAIM = re.compile(
    r"\b(?:a|an|our|the)\s+(?:member\s+of\s+(?:the\s+|our\s+)?(?:support\s+)?(?:team|staff)|"
    r"(?:support\s+|customer[-\s]care\s+|human\s+)?(?:agent|team|specialist|representative|"
    r"person|colleague|staff\s+member)s?)\s+(?:will|['’]ll|is\s+going\s+to|"
    r"are\s+going\s+to|shall)\s+(?:\w+\s+){0,2}?(?:review|look|contact|get\s+back|reach|"
    r"process|handle|call|email)"
    r"|\byou['’]ll\s+hear\s+(?:back|from)",
    re.IGNORECASE,
)
# "waiting for your approval", "approve the draft"...
_WAITING_CLAIM = re.compile(
    r"\b(?:awaiting|waiting\s+for|pending)\s+(?:your\s+|the\s+customer['’]s\s+)?approv\w*"
    r"|\bready\s+for\s+(?:your\s+)?approv\w*"
    r"|\bapprove\s+(?:it|the\s+draft|this\s+draft|the\s+ticket)\b"
    r"|\b(?:use|click|press|tap)\s+the\s+\W*approve\W*\s+(?:control|button)"
    r"|\bconfirm\s+(?:the|this|your)\s+draft\b",
    re.IGNORECASE,
)
# Words earlier in the same sentence that make a claim conditional or negative: "once you
# approve it, a support agent will review it", "nothing is submitted until...".
_CONDITIONAL = re.compile(
    r"\b(?:once|after|when|if|until|unless|only|not|nothing|never|no|unable|cannot)\b"
    r"|n['’]t\b",
    re.IGNORECASE,
)
# A past-tense claim stays a claim after an "if" (Session 13, r19): "If you'd like to
# proceed, use the Approve control on the draft ticket that was created".
_PAST = re.compile(
    r"\b(?:was|were|has been|have been|i['\u2019]ve|i have|we['\u2019]ve|we have)\b",
    re.IGNORECASE,
)
# The answer mentions tickets or refunds: only then is the small-model action check asked.
_ACTION_TOPIC = re.compile(r"\b(?:tickets?|refunds?|refunded)\b", re.IGNORECASE)
# A negation inside the claim itself: "I'm unable to submit the ticket", "I have not sent".
_NEGATED = re.compile(r"\b(?:unable|cannot|not|never)\b|n['’]t\b", re.IGNORECASE)


def _sentence_before(text: str, start: int) -> str:
    """The part of ``start``'s sentence before it."""
    return re.split(r"[.!?\n]", text[:start])[-1]


def _exempt(text: str, start: int) -> bool:
    """A conditional or negation earlier in the sentence exempts a claim, unless the
    sentence also makes a past-tense claim ("If you'd like to proceed, use the Approve
    control on the draft ticket that was created": Session 13, r19)."""
    before = _sentence_before(text, start)
    if not _CONDITIONAL.search(before):
        return False
    rest = re.split(r"[.!?\n]", text[start:])[0]
    return not _PAST.search(before + rest)


ActionChecker = Callable[[str], bool]  # True: the answer promises or claims an action


def make_action_checker(model: BaseChatModel, template: str) -> ActionChecker:
    """The small model as a yes/no checker (Session 13): does ``answer`` say the assistant
    took, or will take, an action on a ticket or refund? Only the first word counts; an
    unclear reply or an API error counts as NO, so the deterministic checks still decide."""
    from rag_assistant.llm import complete

    def check(answer: str) -> bool:
        try:
            reply = complete(model, template.format(answer=answer))  # type: ignore[arg-type]
        except Exception:  # noqa: BLE001 - fail open: the rule-based checks still apply
            return False
        words = reply.strip().split()
        return bool(words) and words[0].strip(".,:*").upper() == "YES"

    return check


def needs_action_check(answer: str, drafts: Sequence[Draft]) -> bool:
    """Ask the checker only when the answer mentions tickets or refunds and no draft was
    made this turn (a draft is the one action an answer may describe)."""
    return not drafts and bool(_ACTION_TOPIC.search(answer))


def approval_claim_problems(answer: str, drafts: Sequence[Draft]) -> list[str]:
    """v9: what the answer claims about tickets must be true this turn.

    - A **submission** ("I've submitted your ticket", "a ticket has been raised") is never
      true in a model answer: only the submit step, our code, submits, and its reply is a
      fixed template. A claim inside a conditional or a negation ("nothing is submitted
      until you approve it") is not a claim.
    - A **draft** claim needs a draft made this turn.
    - A **review** promise ("a support agent will review it") is a submission claim in
      disguise, unless conditional ("once you approve it, ...").
    - **Waiting for approval** needs a draft made this turn.
    """
    problems, drafted = [], bool(drafts)
    for match in _ACTION_CLAIM.finditer(answer):
        span = match.group(0)
        if _exempt(answer, match.start()) or _NEGATED.search(span):
            continue
        if "draft" in span.lower() and not _SUBMIT_WORD.search(span):
            if not drafted:
                problems.append(f'claims a draft ("{span}"), but draft_ticket drafted none')
            continue
        problems.append(f'claims a submitted ticket ("{span}"), but nothing was submitted')
    for match in _REVIEW_CLAIM.finditer(answer):
        if not _exempt(answer, match.start()):
            problems.append(f'promises a review ("{match.group(0)}"), but nothing was submitted')
    if not drafted:
        problems += [
            f'mentions approval ("{m.group(0)}"), but no draft is waiting'
            for m in _WAITING_CLAIM.finditer(answer)
            if not _exempt(answer, m.start())
        ]
    return problems


# ------------------------------------------------------------------ the graph


class AgentState(TypedDict, total=False):
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
    # v9
    owner: str  # the user the thread belongs to
    pending_draft: dict | None  # Draft.as_dict() of the draft awaiting approval
    decision: dict | None  # the client's resume value
    submission: dict | None  # Submission.as_dict() of this resume
    checker_calls: int  # Session 13: small-model action checks this turn


@dataclass(frozen=True)
class AgentResult:
    """One agent turn (or one approval decision): the reply, the tool calls, the route."""

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
    # v9
    thread_id: str = ""
    pending_draft: dict | None = None  # the draft now waiting for approval
    submission: dict | None = None  # what an approval did
    superseded: str | None = None  # the draft id this turn cancelled
    drafts: list[dict] = field(default_factory=list)  # every draft made this turn
    checker_calls: int = 0  # Session 13: small-model action checks this turn

    @property
    def tools_used(self) -> list[str]:
        """Tool names in call order."""
        return [c.name for c in self.calls]


@dataclass(frozen=True)
class Snapshot:
    """A thread's saved state: whose it is, and the draft waiting for approval, if any."""

    owner: str
    pending_draft: dict | None


def _history(turns: Sequence[Turn]) -> list[BaseMessage]:
    """Earlier turns as chat messages."""
    return [HumanMessage(t.content) if t.role == "user" else AIMessage(t.content) for t in turns]


def _text(message: BaseMessage) -> str:
    """A message's text content."""
    return message.content if isinstance(message.content, str) else str(message.content)


def answer_without_tools(model: BaseChatModel, messages: list[BaseMessage]) -> AIMessage:
    """One answer with no tools bound (finalize and the guard's regeneration).

    The model sometimes asks for a tool anyway; Groq then rejects the reply with
    ``tool_use_failed`` (Session 11, g026). That counts as no answer, so the guard fails it
    and the fixed reply is sent, instead of the run crashing.
    """
    try:
        reply = model.invoke(messages)
    except groq.BadRequestError as exc:
        if "tool_use_failed" not in str(exc):
            raise
        return AIMessage("")
    return reply if isinstance(reply, AIMessage) else AIMessage(_text(reply))


def new_thread_id() -> str:
    """A fresh conversation thread id."""
    return f"thread-{uuid.uuid4().hex[:12]}"


class AgentRunner:
    """Runs the agent graph for one session: a question, or a decision on a paused thread."""

    def __init__(
        self,
        model: BaseChatModel,
        prompts: dict[str, str],
        guard: OutputGuard | None,
        max_tool_calls: int,
        max_generations: int = 3,
        submitter: Submitter | None = None,
        checkpointer: BaseCheckpointSaver | None = None,
        tool_factory: Callable[[AgentSession], list[StructuredTool]] = make_tools,
        action_checker: ActionChecker | None = None,
    ) -> None:
        """``model`` must support ``bind_tools``; the limits come from the stage and config.

        A ``submitter`` turns on v9's approval flow; it needs a checkpointer (in memory if
        none is given) to pause and resume. ``tool_factory`` builds the session's tools:
        in-process (``make_tools``) or, in v10, over MCP (``mcp_server.client``).
        ``action_checker`` (approval stages, Session 13) is the small-model yes/no check
        for promised actions.
        """
        if max_tool_calls < 1 or max_generations < 1:
            raise ValueError("max_tool_calls and max_generations must be at least 1")
        self._model = model
        self._prompts = prompts
        self._guard = guard
        self._max_tool_calls = max_tool_calls
        self._max_generations = max_generations
        self.submitter = submitter
        self.approval = submitter is not None
        self._checkpointer = checkpointer or (InMemorySaver() if self.approval else None)
        self._tool_factory = tool_factory
        self._action_checker = action_checker

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
            reply = answer_without_tools(self._model, [*state["messages"], note])
            return {
                **update,
                "messages": [*state["messages"], note, reply],
                "final": _text(reply).strip(),
                "generations": state["generations"] + 1,
                "model_calls": state["model_calls"] + 1,
            }

        def trusted_results() -> list[str]:
            """This turn's order-tool results; a draft's free-text reason is left out."""
            texts = []
            for c in session.calls:
                if c.name == KNOWLEDGE_TOOL:
                    continue
                result = c.result
                if c.name == DRAFT_TOOL and isinstance(result.get("ticket"), dict):
                    result = {**result, "ticket": {**result["ticket"], "reason": ""}}
                texts.append(json.dumps(result))
            return texts

        def guard(state: AgentState) -> dict:
            if not state["final"]:
                return {"guard_passed": False}
            problems: list[str] = []
            if self._guard is not None:
                verdict = self._guard.check(
                    state["final"],
                    list(range(1, len(session.passages) + 1)),
                    session.passages,
                    trusted_results(),
                    check_dates=True,
                )
                problems = list(verdict.problems)
            if self.approval:
                problems += approval_claim_problems(state["final"], session.drafts)
            else:
                problems += claimed_ticket_problems(state["final"], session.calls)
            checks = state.get("checker_calls", 0)
            checker = self._action_checker
            asks = self.approval and checker is not None and not problems
            if asks and needs_action_check(state["final"], session.drafts):
                checks += 1
                if checker(state["final"]):  # type: ignore[misc]
                    problems.append(
                        "promises or claims an action on a ticket or refund, but no tool "
                        "took one this turn (action check)"
                    )
            update: dict = {
                "guard_passed": not problems,
                "guard_problems": [*state["guard_problems"], *problems],
                "checker_calls": checks,
            }
            if self.approval and not problems and session.drafts:
                update["pending_draft"] = session.drafts[-1].as_dict()
            return update

        def after_guard(state: AgentState) -> str:
            if state["guard_passed"]:
                return "await_approval" if state.get("pending_draft") else END
            can_retry = not state["guard_used"] and state["generations"] < self._max_generations
            return "regenerate" if can_retry and state["final"] else "fallback"

        def regenerate(state: AgentState) -> dict:
            problems = "; ".join(state["guard_problems"][-3:])
            key = "agent_guard_note_approval" if self.approval else "agent_guard_note"
            note = HumanMessage(self._prompts[key].format(problems=problems))
            reply = answer_without_tools(self._model, [*state["messages"], note])
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

        def await_approval(state: AgentState) -> dict:
            # Re-runs from the top on resume, so nothing with side effects goes before it.
            decision = interrupt({"draft": state["pending_draft"], "answer": state["final"]})
            return {"decision": decision}

        def after_approval(state: AgentState) -> str:
            decision = state.get("decision") or {}
            return "submit" if decision.get("action") == "approve" else "close_draft"

        def submit(state: AgentState) -> dict:
            assert self.submitter is not None
            pending = state["pending_draft"]
            assert pending is not None
            draft = Draft.from_dict(pending)
            decision = state.get("decision") or {}
            result = self.submitter.submit(draft, str(decision.get("hash", "")), session.user)
            if result.ticket is not None:
                key = "approval_submitted" if result.status == "submitted" else "approval_existing"
                text = self._prompts[key].format(**_ticket_fields(asdict(result.ticket)))
            else:
                text = self._prompts["approval_refused"].format(reason=result.reason)
            return {
                "final": text,
                "submission": result.as_dict(),
                "pending_draft": None,
                "outcome": result.status,
            }

        def close_draft(state: AgentState) -> dict:
            pending = state["pending_draft"] or {}
            decision = state.get("decision") or {}
            key = (
                "approval_superseded"
                if decision.get("action") == "superseded"
                else "approval_rejected"
            )
            return {
                "final": self._prompts[key].format(draft_id=pending.get("draft_id", "")),
                "pending_draft": None,
                "outcome": "closed",
            }

        g = StateGraph(AgentState)
        nodes = [
            ("model", model),
            ("tools", run_tools),
            ("finalize", finalize),
            ("guard", guard),
            ("regenerate", regenerate),
            ("fallback", fallback),
        ]
        if self.approval:
            nodes += [
                ("await_approval", await_approval),
                ("submit", submit),
                ("close_draft", close_draft),
            ]
        for name, node in nodes:
            g.add_node(name, node)
        g.add_edge(START, "model")
        g.add_conditional_edges("model", after_model, ["tools", "guard"])
        g.add_conditional_edges("tools", after_tools, ["model", "finalize"])
        g.add_edge("finalize", "guard")
        guard_targets = ["regenerate", "fallback", END]
        if self.approval:
            guard_targets.append("await_approval")
            g.add_conditional_edges("await_approval", after_approval, ["submit", "close_draft"])
            g.add_edge("submit", END)
            g.add_edge("close_draft", END)
        g.add_conditional_edges("guard", after_guard, guard_targets)
        g.add_edge("regenerate", "guard")
        g.add_edge("fallback", END)
        return g

    def _config(self, thread_id: str) -> dict:
        """The run config for a thread."""
        return {
            "configurable": {"thread_id": thread_id},
            "recursion_limit": 4 * (self._max_tool_calls + self._max_generations) + 10,
        }

    def _compiled(self, session: AgentSession) -> CompiledStateGraph:
        """The graph for ``session``, with the checkpointer in v9."""
        return self._build(session, self._tool_factory(session)).compile(
            checkpointer=self._checkpointer
        )

    def run(
        self,
        session: AgentSession,
        question: str,
        history: Sequence[Turn] = (),
        thread_id: str | None = None,
        note: str | None = None,
    ) -> AgentResult:
        """Answer ``question`` for ``session.user``; returns the reply and the trace.

        ``note`` (v9) is a message from our code to the model, sent before the question:
        for example that the user's earlier draft was just cancelled.
        """
        start = time.perf_counter()
        thread_id = thread_id or new_thread_id()
        session.thread_id = thread_id
        role = "agent" if session.user.role == "agent" else "customer"
        key = f"agent_system_{role}_approval" if self.approval else f"agent_system_{role}"
        system = SystemMessage(self._prompts[key].format(today=session.today.isoformat()))
        state = self._compiled(session).invoke(
            {
                "messages": [
                    system,
                    *_history(history),
                    *([SystemMessage(note)] if note else []),
                    HumanMessage(question),
                ],
                "tool_calls_made": 0,
                "generations": 0,
                "model_calls": 0,
                "final": "",
                "hit_step_limit": False,
                "guard_used": False,
                "guard_passed": False,
                "guard_problems": [],
                "outcome": "answered",
                "owner": session.user.user_id,
                "pending_draft": None,
                "decision": None,
                "submission": None,
                "checker_calls": 0,
            },
            self._config(thread_id),
        )
        paused = bool(state.get("__interrupt__"))
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
            thread_id=thread_id,
            pending_draft=state.get("pending_draft") if paused else None,
            drafts=[d.as_dict() for d in session.drafts],
            checker_calls=state.get("checker_calls", 0),
        )

    def snapshot(self, session: AgentSession, thread_id: str) -> Snapshot | None:
        """The saved state of ``thread_id``, or None if there is none."""
        if self._checkpointer is None:
            return None
        state = self._compiled(session).get_state(self._config(thread_id))
        if not state.values:
            return None
        pending = state.values.get("pending_draft") if state.interrupts else None
        return Snapshot(owner=state.values.get("owner", ""), pending_draft=pending)

    def resume(self, session: AgentSession, thread_id: str, decision: dict) -> AgentResult:
        """Resume a paused thread with the client's ``decision`` (our code, never the model)."""
        if not self.approval:
            raise RuntimeError("only an approval stage pauses")
        start = time.perf_counter()
        session.thread_id = thread_id
        state = self._compiled(session).invoke(Command(resume=decision), self._config(thread_id))
        return AgentResult(
            text=state["final"],
            calls=[],
            passages=[],
            tool_calls=0,
            model_calls=0,
            generations=0,
            hit_step_limit=False,
            guard_problems=[],
            outcome=state["outcome"],
            ms=round((time.perf_counter() - start) * 1000, 1),
            thread_id=thread_id,
            submission=state.get("submission"),
        )


def _ticket_fields(ticket: dict) -> dict:
    """A ticket record's fields for the confirmation templates."""
    return {**ticket, "order_id": ticket.get("order_id") or "no order"}


# ------------------------------------------------------------- the pipeline face


@dataclass(frozen=True)
class ApprovalResult:
    """What an approve or reject request did (for chat.py and the UI)."""

    status: Literal["submitted", "existing", "refused", "rejected"]
    text: str
    ticket: dict | None = None
    reason: str = ""


class AgentPipeline:
    """The agent with ``Pipeline``'s interface: ``answer()`` runs it for the user.

    Its knowledge tool is the stage's own v7 pipeline; the last knowledge call's
    retrieval fills the ``Answer``'s context fields, so the golden-set metrics still work.
    In an approval stage, ``pending``, ``approve`` and ``reject`` are the client API.
    In v10 the tools run in the MCP server (``mcp``, a ``MCPToolClient``), so there is no
    local knowledge pipeline; the retrieval context comes back in the tool results.
    """

    def __init__(
        self,
        stage: StageConfig,
        knowledge: Pipeline | None,
        runner: AgentRunner,
        orders: OrderStore,
        rules: BusinessRules,
        catalogue: dict[str, Product],
        audit: AuditLog | None = None,
        mcp: object | None = None,
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
        self.audit = audit
        self.mcp = mcp

    @classmethod
    def from_stage(
        cls,
        stage: str | StageConfig,
        settings: Settings | None = None,
        tickets: TicketStore | None = None,
        allow_redteam: bool = False,
        persist: bool = False,
    ) -> AgentPipeline:
        """Build v8 or v9 from config: the v7 knowledge pipeline, the order store, the model.

        In v9, ``persist`` keeps paused threads, tickets and the audit log on disk (chat and
        ask); otherwise (evaluation) they live in memory and nothing is written.
        """
        settings = settings or get_settings()
        stage = load_stage(stage) if isinstance(stage, str) else stage
        knowledge, mcp, tool_factory = None, None, make_tools
        if stage.tools_via_mcp:  # v10: the tools (and the v7 pipeline) run in the server
            from rag_assistant.mcp_server.client import MCPToolClient, mcp_tool_factory

            mcp = (
                MCPToolClient.for_stage(stage.name, None)
                if persist
                else MCPToolClient.for_evaluation(stage.name)
            )
            tool_factory = mcp_tool_factory(mcp)
        else:
            knowledge = Pipeline.from_stage(
                stage, settings, tickets or MemoryTicketStore(), allow_redteam=allow_redteam
            )
        guard, max_generations = make_guard(stage, settings)
        generated = settings.paths.resolve(settings.paths.generated_dir)
        catalogue = {p.code: p for p in load_catalogue(generated / "manifest.json")}
        orders = OrderStore(generated / "orders.db")
        rules = load_business_rules()
        submitter, checkpointer, audit = None, None, None
        if stage.approval:
            cfg = settings.approval
            if persist:
                state_path = settings.paths.resolve(cfg.checkpoint_path)
                state_path.parent.mkdir(parents=True, exist_ok=True)
                checkpointer = SqliteSaver(sqlite3.connect(state_path, check_same_thread=False))
                ticket_db = TicketDB(settings.paths.resolve(cfg.tickets_db))
                audit = FileAudit(settings.paths.resolve(cfg.audit_path))
            else:
                checkpointer, ticket_db, audit = InMemorySaver(), TicketDB(), MemoryAudit()
            submitter = Submitter(ticket_db, orders, rules, catalogue, cfg.max_tickets_per_day)
        prompts = load_prompts()
        checker = (
            make_action_checker(
                make_chat_model(settings, settings.llm.small_model), prompts["action_check"]
            )
            if stage.approval
            else None
        )
        runner = AgentRunner(
            make_chat_model(settings, settings.llm.large_model),
            prompts,
            guard,
            int(stage.max_tool_calls or 1),
            max_generations,
            submitter,
            checkpointer,
            tool_factory,
            checker,
        )
        return cls(stage, knowledge, runner, orders, rules, catalogue, audit, mcp)

    def __enter__(self) -> AgentPipeline:
        """Enter the context manager."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close the knowledge pipeline and the databases."""
        self.close()

    def close(self) -> None:
        """Release the Weaviate clients, the MCP servers and the database connections."""
        if self._knowledge is not None:
            self._knowledge.close()
        if self.mcp is not None:
            self.mcp.close()  # type: ignore[attr-defined]
        self._orders.close()
        if self.submitter is not None:
            self.submitter.tickets.close()

    @property
    def submitter(self) -> Submitter | None:
        """The submit step (v9), or None."""
        return self._runner.submitter

    @property
    def prompts(self) -> dict[str, str]:
        """The loaded prompt templates."""
        return self._runner._prompts  # noqa: SLF001 - the runner owns them

    def _session(
        self, user: User, retrieve_k: int | None = None
    ) -> tuple[AgentSession, list[RetrievalContext]]:
        """A fresh tool session for ``user``, and the list its retrieval contexts go to."""

        def no_local_knowledge(q: str) -> KnowledgeResult:
            raise RuntimeError("v10's knowledge tool runs in the MCP server")

        session = AgentSession(
            user,
            self._orders,
            self._rules,
            self._catalogue,
            no_local_knowledge,
            approval=self.stage.approval,
            audit=None if self.stage.tools_via_mcp else self.audit,  # the server audits v10's
        )
        if self._knowledge is not None:
            session.knowledge = make_knowledge(self._knowledge, user, session.contexts, retrieve_k)
        return session, session.contexts

    def _log(self, event: str, user: User, thread_id: str, **fields: object) -> None:
        """Write an audit event, if there is a log."""
        if self.audit is not None:
            self.audit.write(event, user.user_id, thread_id, **fields)

    def _owned(self, user: User, thread_id: str) -> Snapshot | None:
        """The thread's snapshot if it belongs to ``user``, else None."""
        snap = self._runner.snapshot(self._session(user)[0], thread_id)
        return snap if snap is not None and snap.owner == user.user_id else None

    def answer(
        self,
        question: str,
        user: User | None = None,
        history: Sequence[Turn] = (),
        retrieve_k: int | None = None,
        thread_id: str | None = None,
    ) -> Answer:
        """Run the agent for ``user`` and return its reply as an ``Answer``.

        In v9 a new message on a thread first cancels any draft still waiting for approval
        (``superseded``), so a changed request needs a new draft and the old one can never
        be approved. A thread that belongs to another user is refused.
        """
        if user is None:
            raise ValueError("the agent needs a signed-in user")
        superseded = None
        if self.stage.approval and thread_id is not None:
            snap = self._runner.snapshot(self._session(user)[0], thread_id)
            if snap is not None and snap.owner != user.user_id:
                raise PermissionError("this conversation belongs to another user")
            if snap is not None and snap.pending_draft is not None:
                superseded = snap.pending_draft["draft_id"]
                self._runner.resume(self._session(user)[0], thread_id, {"action": "superseded"})
                self._log("superseded", user, thread_id, draft_id=superseded)
        session, last = self._session(user, retrieve_k)
        note = (
            self.prompts["approval_superseded_note"].format(draft_id=superseded)
            if superseded
            else None
        )
        result = self._runner.run(session, question, history, thread_id, note)
        if result.pending_draft is not None:
            draft = result.pending_draft
            self._log(
                "pause",
                user,
                result.thread_id,
                draft_id=draft["draft_id"],
                hash=draft["hash"],
                order_id=draft["order_id"],
                kind=draft["kind"],
            )
        text = result.text
        if superseded is not None:
            note = self.prompts["approval_superseded"].format(draft_id=superseded)
            text = f"{note}\n\n{text}"
            result = replace(result, text=text, superseded=superseded)
        kb = last[-1] if last else None
        return Answer(
            text=text,
            context=kb.context if kb else [],
            retrieved=kb.retrieved if kb else [],
            timings_ms={"total": result.ms, "agent_model_calls": float(result.model_calls)},
            candidates=kb.candidates if kb else [],
            rewrite=kb.rewrite if kb else None,
            region_filter=kb.region_filter if kb else None,
            security_events=sum(c.security_events for c in last),
            model_text=None if result.outcome == "fallback" else result.text,
            agent=result,
        )

    # ---------------------------------------------------------- the client API (v9)

    def pending(self, thread_id: str, user: User) -> dict | None:
        """The draft waiting for ``user``'s approval on ``thread_id``, or None."""
        snap = self._owned(user, thread_id) if self.stage.approval else None
        return None if snap is None else snap.pending_draft

    def _not_waiting(self, user: User, thread_id: str, draft_id: str) -> ApprovalResult:
        """Refuse a decision on a draft that isn't waiting for this user here."""
        reason = "no draft with that id is waiting for your approval in this conversation"
        self._log("refusal", user, thread_id, draft_id=draft_id, reason=reason)
        return ApprovalResult("refused", self.prompts["approval_refused"].format(reason=reason))

    def approve(self, thread_id: str, user: User, draft_id: str, draft_hash: str) -> ApprovalResult:
        """Approve the draft the client showed (its id and hash) and submit it."""
        pending = self.pending(thread_id, user)
        if pending is None or pending["draft_id"] != draft_id:
            earlier = self.submitter.tickets.by_hash(draft_hash) if self.submitter else None
            if earlier is not None and (earlier.draft_id, earlier.created_by) == (
                draft_id,
                user.user_id,
            ):  # approving the same draft twice: the same ticket, nothing new
                record = asdict(earlier)
                text = self.prompts["approval_existing"].format(**_ticket_fields(record))
                self._log(
                    "submission", user, thread_id, status="existing", ticket=earlier.ticket_id
                )
                return ApprovalResult("existing", text, record)
            return self._not_waiting(user, thread_id, draft_id)
        self._log("approve", user, thread_id, draft_id=draft_id, hash=draft_hash)
        decision = {"action": "approve", "hash": draft_hash}
        result = self._runner.resume(self._session(user)[0], thread_id, decision)
        sub = result.submission or {"status": "refused", "ticket": None, "reason": "no result"}
        if sub["ticket"] is not None:
            ticket_id = sub["ticket"]["ticket_id"]
            self._log("submission", user, thread_id, status=sub["status"], ticket=ticket_id)
        else:
            self._log("refusal", user, thread_id, draft_id=draft_id, reason=sub["reason"])
        return ApprovalResult(sub["status"], result.text, sub["ticket"], sub["reason"])

    def reject(self, thread_id: str, user: User, draft_id: str) -> ApprovalResult:
        """Discard the waiting draft; nothing is submitted."""
        pending = self.pending(thread_id, user)
        if pending is None or pending["draft_id"] != draft_id:
            return self._not_waiting(user, thread_id, draft_id)
        self._log("reject", user, thread_id, draft_id=draft_id)
        result = self._runner.resume(self._session(user)[0], thread_id, {"action": "reject"})
        return ApprovalResult("rejected", result.text)


def open_pipeline(
    stage: StageConfig,
    settings: Settings,
    tickets: TicketStore | None = None,
    allow_redteam: bool = False,
    persist: bool = False,
) -> Pipeline | AgentPipeline:
    """The stage's pipeline: the agent for ``agent`` stages, else the v0-v7 pipeline."""
    if stage.agent:
        return AgentPipeline.from_stage(stage, settings, tickets, allow_redteam, persist)
    return Pipeline.from_stage(stage, settings, tickets, allow_redteam=allow_redteam)

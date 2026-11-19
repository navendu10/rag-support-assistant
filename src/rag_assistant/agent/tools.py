"""The agent's tools (Session 10): knowledge search, read-only order tools, draft tickets.

``make_tools(session)`` builds the tools for **one** signed-in user. The user lives in the
``AgentSession``, injected by our code; no tool takes a user or customer id from the
model, and a test checks every input model for one. Each tool returns JSON:

- ``search_knowledge_base(question)`` runs the v7 pipeline for the session user (same
  role, Weaviate key, collections, region filter and injection layers) and returns its
  answer and the passages it cited, numbered K1, K2... across the turn. A v7 handoff
  comes back as ``status: not_found`` and writes no ticket.
- ``list_my_orders``, ``get_order``, ``check_warranty`` and ``check_return_eligibility``
  read the order database (read-only) and compute dates against the business rules'
  reference date, so the model never does date arithmetic.
- ``draft_ticket`` returns a draft and **writes nothing**. The ticket's customer comes
  from the order record. In an approval stage (v9, Session 11) it can also draft a
  ``handoff`` (no order needed), each draft gets an id, and the graph then pauses for the
  user's approval; there is no tool that submits.

An order the user may not see returns exactly what a missing order returns. Tool results
only hold our database fields and values our code computed: there is no free text anyone
else could write. Each result has a ``facts`` list in plain words ("15 days", "24
months"), which the output guard reads as trusted.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field, model_validator

from rag_assistant.agent.audit import AuditLog, tool_fields
from rag_assistant.agent.orders import Order, OrderStore
from rag_assistant.agent.rules import BusinessRules, return_window, warranty_periods
from rag_assistant.agent.tickets import Draft, new_draft_id
from rag_assistant.retrieval.vector import RetrievedChunk
from rag_assistant.schemas import Product, User
from rag_assistant.security.sanitize import strip_invisible

# ----------------------------------------------------------------- inputs


class NoInput(BaseModel):
    """No arguments."""

    model_config = ConfigDict(extra="forbid")


class KnowledgeInput(BaseModel):
    """A question for VoltCart's help documents."""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=500, description="A standalone question")


class OrderInput(BaseModel):
    """One order, by its id."""

    model_config = ConfigDict(extra="forbid")

    order_id: str = Field(description="The order id, for example VC-10001")


class DraftTicketInput(BaseModel):
    """A draft support ticket for one order."""

    model_config = ConfigDict(extra="forbid")

    order_id: str = Field(description="The order the ticket is about")
    kind: Literal["repair", "return"] = Field(description="repair or return")
    reason: str = Field(min_length=1, max_length=300, description="One sentence: why")


class ApprovalDraftInput(BaseModel):
    """A draft ticket in an approval stage: repair or return for an order, or a handoff."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["repair", "return", "handoff"] = Field(
        description="repair or return (needs an order), or handoff to a person"
    )
    order_id: str | None = Field(default=None, description="The order; optional for a handoff")
    reason: str = Field(min_length=1, max_length=300, description="One sentence: why")

    @model_validator(mode="after")
    def _order_for_repairs_and_returns(self) -> ApprovalDraftInput:
        """A repair or return is always about one order."""
        if self.kind != "handoff" and not self.order_id:
            raise ValueError(f"a {self.kind} ticket needs an order_id")
        return self


# ---------------------------------------------------------------- session


@dataclass(frozen=True)
class KnowledgeResult:
    """What the v7 pipeline returned for one knowledge question."""

    answered: bool
    answer: str
    passages: list[RetrievedChunk]  # the passages its answer cited


@dataclass(frozen=True)
class ToolCall:
    """One tool call and its result (the agent trace)."""

    name: str
    args: dict
    result: dict
    ms: float


@dataclass
class AgentSession:
    """Everything the tools need for one signed-in user; built by our code, not the model."""

    user: User
    orders: OrderStore
    rules: BusinessRules
    catalogue: dict[str, Product]
    knowledge: Callable[[str], KnowledgeResult]
    calls: list[ToolCall] = field(default_factory=list)
    passages: list[RetrievedChunk] = field(default_factory=list)  # K1, K2, ... this turn
    # Session 11 (approval stages only): the conversation thread, the drafts made this
    # turn (the last one is the one the user is asked to approve), and the audit log.
    approval: bool = False
    thread_id: str = ""
    drafts: list[Draft] = field(default_factory=list)
    audit: AuditLog | None = None

    @property
    def today(self) -> date:
        """The fixed reference date all tools compute against."""
        return self.rules.reference_date


NOT_FOUND = "No order with this id was found on your account."


def not_found(order_id: str) -> dict:
    """The result for an order the user may not see, identical to a missing order."""
    return {"status": "not_found", "order_id": order_id, "message": NOT_FOUND}


def _product(session: AgentSession, code: str) -> str:
    """``VisionMax 55" 4K TV (VM-55Q)``, or the bare code if it isn't in the catalogue."""
    product = session.catalogue.get(code)
    return f"{product.name} ({code})" if product else code


def _category(session: AgentSession, code: str) -> str:
    """The catalogue category of ``code`` (default rules if unknown)."""
    product = session.catalogue.get(code)
    return product.category if product else "default"


def _iso(value: date | None) -> str | None:
    """A date as ISO text, or None."""
    return None if value is None else value.isoformat()


def order_summary(session: AgentSession, order: Order) -> dict:
    """The fields of an order a customer may see."""
    line = order.lines[0]
    summary = {
        "order_id": order.order_id,
        "product": _product(session, line.product_code),
        "product_code": line.product_code,
        "price": line.price,
        "ordered": _iso(order.ordered),
        "estimated_delivery": _iso(order.estimated_delivery),
        "delivered": _iso(order.delivered),
        "status": order.status,
        "opened": line.opened,
        "region": order.region,
    }
    if session.user.role == "agent":  # staff may see whose order it is
        summary["customer_id"] = order.customer_id
    return summary


# ----------------------------------------------------------- tool logic


def list_my_orders(session: AgentSession) -> dict:
    """Every order on the signed-in customer's account (none for staff)."""
    orders = session.orders.mine(session.user)
    result: dict = {"status": "ok", "orders": [order_summary(session, o) for o in orders]}
    if session.user.role != "customer":
        result["note"] = (
            "Staff accounts have no orders of their own. Look up a specific order by its "
            "id with get_order."
        )
    return result


def get_order(session: AgentSession, order_id: str) -> dict:
    """One order's details, including how late it was delivered."""
    order = session.orders.find(order_id, session.user)
    if order is None:
        return not_found(order_id)
    facts = [f"ordered on {_iso(order.ordered)}", f"status: {order.status}"]
    if order.delivered:
        facts.append(f"delivered on {_iso(order.delivered)}")
        if order.late_by_days:
            facts.append(
                f"delivered {order.late_by_days} days after the estimated delivery date "
                f"({_iso(order.estimated_delivery)})"
            )
    else:
        facts.append(f"not delivered yet; estimated delivery {_iso(order.estimated_delivery)}")
    return {
        "status": "ok",
        "order": order_summary(session, order),
        "late_by_days": order.late_by_days,
        "facts": facts,
    }


def check_warranty(session: AgentSession, order_id: str) -> dict:
    """The VoltCart warranty periods of an order, as of the reference date."""
    order = session.orders.find(order_id, session.user)
    if order is None:
        return not_found(order_id)
    line = order.lines[0]
    base = {"order_id": order.order_id, "product": _product(session, line.product_code)}
    if line.product_code in session.rules.warranty.marketplace_codes:
        return {
            **base,
            "status": "seller_terms",
            "facts": ["a marketplace item: the warranty is set by the seller, not VoltCart"],
        }
    if order.delivered is None:
        return {**base, "status": "not_delivered", "facts": ["the warranty starts on delivery"]}
    periods = warranty_periods(line.product_code, order.delivered, session.rules, session.today)
    return {
        **base,
        "status": "ok",
        "delivered": _iso(order.delivered),
        "reference_date": _iso(session.today),
        "periods": [
            {"covers": p.covers, "months": p.months, "ends": _iso(p.ends), "covered": p.covered}
            for p in periods
        ],
        "facts": [
            f"{p.covers}: {p.months} months from delivery, until {_iso(p.ends)}, "
            f"{'covered' if p.covered else 'expired'}"
            for p in periods
        ],
    }


def check_return_eligibility(session: AgentSession, order_id: str) -> dict:
    """Whether an order can still be returned, the last day, and any fee."""
    order = session.orders.find(order_id, session.user)
    if order is None:
        return not_found(order_id)
    line = order.lines[0]
    base = {"order_id": order.order_id, "product": _product(session, line.product_code)}
    if line.product_code in session.rules.warranty.marketplace_codes:
        return {
            **base,
            "status": "seller_terms",
            "facts": ["a marketplace item: returns follow the seller's terms on the listing"],
        }
    if order.delivered is None:
        return {
            **base,
            "status": "not_delivered",
            "facts": ["the return window starts on delivery"],
        }
    window = return_window(
        order.region,
        _category(session, line.product_code),
        bool(line.opened),
        order.delivered,
        session.rules,
        session.today,
    )
    facts = [
        f"{window.condition} item: {window.window_days} days from delivery to return",
        f"delivered on {_iso(order.delivered)}, {window.days_since_delivery} days before "
        f"{_iso(session.today)}",
        f"last day to return: {_iso(window.last_day)}",
        "eligible" if window.eligible else "not eligible: the return window has closed",
    ]
    if window.fee_percent:
        facts.append(f"a {window.fee_percent}% restocking fee applies")
    if window.eligible:
        facts.append(
            f"refund within {session.rules.returns.refund_working_days} working days after "
            "the return passes inspection"
        )
    return {
        **base,
        "status": "ok",
        "condition": window.condition,
        "window_days": window.window_days,
        "delivered": _iso(order.delivered),
        "last_day": _iso(window.last_day),
        "days_since_delivery": window.days_since_delivery,
        "eligible": window.eligible,
        "restocking_fee_percent": window.fee_percent,
        "original_packaging_required": window.original_packaging_required,
        "facts": facts,
    }


_TAG = re.compile(r"<[^>]{0,200}>")


def clean_reason(text: str) -> str:
    """A ticket reason as plain visible text: no invisible characters, no markup, one line.

    The reason is the one free-text field the model writes into a draft, so it is shown to
    the user exactly as it will be stored, and it is never a trusted source for the guard.
    """
    return " ".join(_TAG.sub(" ", strip_invisible(text)).split())


def approval_draft(session: AgentSession, kind: str, order_id: str | None, reason: str) -> dict:
    """v9: a draft the user must approve. Nothing is written or submitted."""
    user, text = session.user, clean_reason(reason)
    if not text:
        return {"status": "error", "message": "the reason is empty after cleaning"}
    if order_id:
        order = session.orders.find(order_id, user)
        if order is None:
            return not_found(order_id)
        customer, code, region = order.customer_id, order.lines[0].product_code, order.region
        order_id = order.order_id
    elif user.role != "customer":
        return {"status": "error", "message": "staff handoff tickets need the order id"}
    else:
        customer, code, region = user.user_id, None, user.region
    draft = Draft(
        draft_id=new_draft_id(),
        thread_id=session.thread_id,
        kind=kind,
        order_id=order_id,
        customer_id=customer,
        product_code=code,
        region=region,
        reason=text,
        drafted_by=user.user_id,
    )
    session.drafts.append(draft)
    return {
        "status": "draft",
        "written": False,
        "ticket": {k: v for k, v in draft.as_dict().items() if k not in ("hash", "thread_id")},
        "facts": [
            "this is a draft: nothing is submitted unless the customer approves it with the "
            "Approve control; you cannot submit it"
        ],
    }


def draft_ticket(session: AgentSession, order_id: str, kind: str, reason: str) -> dict:
    """A draft ticket for one order. Nothing is written or submitted."""
    order = session.orders.find(order_id, session.user)
    if order is None:
        return not_found(order_id)
    return {
        "status": "draft",
        "written": False,
        "ticket": {
            "kind": kind,
            "order_id": order.order_id,
            "customer_id": order.customer_id,  # from the order record, never from the model
            "product_code": order.lines[0].product_code,
            "region": order.region,
            "reason": reason,
            "drafted_by": session.user.user_id,
        },
        "facts": ["this is a draft: nothing has been submitted, and a person must approve it"],
    }


def search_knowledge_base(session: AgentSession, question: str) -> dict:
    """The v7 pipeline's answer for the session user, with the passages it cited."""
    result = session.knowledge(question)
    passages = []
    for chunk in result.passages:
        session.passages.append(chunk)
        passages.append(
            {
                "id": f"K{len(session.passages)}",
                "title": chunk.title,
                "doc_id": chunk.doc_id,
                "trust": chunk.trust,
                "text": chunk.text,
            }
        )
    return {
        "status": "answered" if result.answered else "not_found",
        "answer": result.answer,
        "passages": passages,
        "note": "" if result.answered else (
            HANDOFF_NOTE if session.approval else
            "The help documents don't answer this. Don't guess: say so, and offer to draft "
            "a ticket if an order is involved."
        ),
    }  # fmt: skip


HANDOFF_NOTE = (
    "The help documents don't answer this. Don't guess: say so, and offer to draft a handoff "
    "ticket (draft_ticket with kind handoff) so a person can answer it."
)


# ------------------------------------------------------------- tool objects

TOOL_SPECS: dict[str, tuple[type[BaseModel], Callable[..., dict], str]] = {
    "search_knowledge_base": (
        KnowledgeInput,
        search_knowledge_base,
        "Search VoltCart's help documents (policies, manuals, FAQs) for a policy or product "
        "question. Returns an answer and numbered passages (K1, K2...) to cite.",
    ),
    "list_my_orders": (
        NoInput,
        list_my_orders,
        "List the signed-in customer's own orders.",
    ),
    "get_order": (
        OrderInput,
        get_order,
        "Get one order's details: product, dates, status, and whether it was delivered late.",
    ),
    "check_warranty": (
        OrderInput,
        check_warranty,
        "Check an order's VoltCart warranty: each period, its end date, and whether it covers "
        "today.",
    ),
    "check_return_eligibility": (
        OrderInput,
        check_return_eligibility,
        "Check whether an order can still be returned: the window, the last day, and any "
        "restocking fee.",
    ),
    "draft_ticket": (
        DraftTicketInput,
        draft_ticket,
        "Draft a repair or return ticket for an order. It is only a draft: nothing is "
        "submitted. When the customer needs a repair or return and the check tools show it "
        "is eligible, call draft_ticket and show the draft for confirmation. Never say a "
        "ticket exists unless draft_ticket returned one.",
    ),
}


# Approval stages (v9): the same tools, with draft_ticket able to draft a handoff. There
# is still no tool that submits, approves or writes anything.
APPROVAL_TOOL_SPECS: dict[str, tuple[type[BaseModel], Callable[..., dict], str]] = {
    **TOOL_SPECS,
    "draft_ticket": (
        ApprovalDraftInput,
        approval_draft,
        "Prepare a DRAFT ticket: repair or return for an order, or handoff to a person when "
        "the help documents can't answer. Draft a repair or return only when the check tools "
        "show it is eligible. You cannot submit it: the customer must approve the exact draft "
        "with the Approve control, and only then is it submitted. Never say a ticket was "
        "created, submitted or will be reviewed.",
    ),
}


def tool_specs(
    session: AgentSession,
) -> dict[str, tuple[type[BaseModel], Callable[..., dict], str]]:
    """The tool table for this session's stage."""
    return APPROVAL_TOOL_SPECS if session.approval else TOOL_SPECS


def _tool(session: AgentSession, name: str) -> StructuredTool:
    """A LangChain tool that runs ``name`` for this session and records the call."""
    model, logic, description = tool_specs(session)[name]

    def run(**kwargs: object) -> str:
        start = time.perf_counter()
        args = model.model_validate(kwargs).model_dump()
        result = logic(session, **args)
        ms = round((time.perf_counter() - start) * 1000, 1)
        session.calls.append(ToolCall(name, args, result, ms))
        if session.audit is not None:
            fields = tool_fields(name, args, result)
            session.audit.write("tool_call", session.user.user_id, session.thread_id, **fields)
        return json.dumps(result, ensure_ascii=False)

    return StructuredTool.from_function(
        func=run, name=name, description=description, args_schema=model
    )


def make_tools(session: AgentSession) -> list[StructuredTool]:
    """Every tool, bound to ``session`` (the signed-in user)."""
    return [_tool(session, name) for name in tool_specs(session)]

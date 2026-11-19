"""Pydantic models shared by the data generators, ingestion and evaluation.

Defines document metadata, users and golden test items, plus small loaders
that read and validate the files that hold them.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Region = Literal["IN", "UK", "US", "ALL"]
DocType = Literal["manual", "policy", "faq", "playbook", "listing"]
Audience = Literal["public", "internal"]
Role = Literal["customer", "agent"]
FailureTag = Literal[
    "chunking",
    "product_code",
    "ranking",
    "query_region",
    "leak",
    "hallucination",
    "injection",
    "stale",
    "normal",
    "agent_access",
]

DOC_ID_PATTERN = r"^[a-z0-9]+(-[a-z0-9]+)*$"


class DocMetadata(BaseModel):
    """Metadata attached to every generated document."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    doc_id: str = Field(pattern=DOC_ID_PATTERN)
    title: str = Field(min_length=1)
    doc_type: DocType
    product_code: str | None
    region: Region
    audience: Audience
    effective_date: date
    source_path: str = Field(min_length=1, description="Path relative to data/generated/")


class Product(BaseModel):
    """One catalogue entry: a product code, its name and its return-table category."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str = Field(min_length=1)
    name: str = Field(min_length=1)
    category: str = Field(min_length=1)


class User(BaseModel):
    """A customer or support agent who can ask the assistant questions."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    role: Role
    region: Region

    @model_validator(mode="after")
    def _region_matches_role(self) -> User:
        """Customers belong to one country; agents serve every region."""
        if self.role == "customer" and self.region == "ALL":
            raise ValueError("customers must have a specific region (IN, UK or US)")
        if self.role == "agent" and self.region != "ALL":
            raise ValueError("agents must have region ALL")
        return self


class Turn(BaseModel):
    """One prior message in a conversation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)


class GoldenItem(BaseModel):
    """One hand-written evaluation question with its expected outcome.

    ``expected_doc_ids`` lists the documents that contain the answer; retrieving
    any one of them counts as a hit. ``must_not_contain`` lists strings that must
    never appear in the assistant's answer (checked case-insensitively).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^g\d{3}$")
    question: str = Field(min_length=1)
    history: list[Turn] = Field(default_factory=list)
    user_id: str = Field(min_length=1)
    failure_tag: FailureTag
    topic: str = Field(min_length=1)
    expected_doc_ids: list[str]
    reference_answer: str = Field(min_length=1)
    expect_handoff: bool
    must_not_contain: list[str]


def load_manifest(path: Path) -> list[DocMetadata]:
    """Read and validate ``manifest.json``."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return [DocMetadata.model_validate(item) for item in data["documents"]]


def load_catalogue(path: Path) -> list[Product]:
    """Read the product catalogue (``products``) from ``manifest.json``."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if "products" not in data:
        raise ValueError(f"{path} has no product catalogue; run scripts/generate_data.py")
    return [Product.model_validate(item) for item in data["products"]]


def load_users(path: Path) -> list[User]:
    """Read and validate ``users.json``."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return [User.model_validate(item) for item in data]


def load_history(path: Path) -> list[Turn]:
    """Read a JSON list of conversation turns (``role``, ``content``)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"{path} must hold a JSON list of turns")
    return [Turn.model_validate(item) for item in data]


def load_golden(path: Path) -> list[GoldenItem]:
    """Read and validate a golden JSONL file, skipping blank lines."""
    lines = path.read_text(encoding="utf-8").splitlines()
    return [GoldenItem.model_validate_json(line) for line in lines if line.strip()]


AGENT_TOOLS = (
    "search_knowledge_base",
    "list_my_orders",
    "get_order",
    "check_warranty",
    "check_return_eligibility",
    "draft_ticket",
)


class ScenarioStep(BaseModel):
    """One step after a scenario's first question (Session 11): a message or a client action.

    - ``say``: the user sends ``text`` (any waiting draft is superseded first).
    - ``approve`` / ``reject``: the client sends a decision on a draft. ``draft`` picks it:
      the ``latest`` draft shown, the ``previous`` one, or the ``other`` thread's draft
      (``setup.other_thread``). ``thread: other`` sends the decision to that thread.
      ``tamper_hash`` sends a hash that differs from the one shown.
    - ``expect``: the status the client must get back (``submitted``, ``existing``,
      ``refused``, ``rejected``).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    action: Literal["say", "approve", "reject"]
    text: str | None = None
    draft: Literal["latest", "previous", "other"] = "latest"
    thread: Literal["own", "other"] = "own"
    tamper_hash: bool = False
    expect: Literal["submitted", "existing", "refused", "rejected"] | None = None

    @model_validator(mode="after")
    def _text_for_messages(self) -> ScenarioStep:
        """A message has text; a decision doesn't."""
        if (self.action == "say") != bool(self.text):
            raise ValueError("a say step needs text, and only a say step has it")
        return self


class OtherThread(BaseModel):
    """A second conversation in the same scenario (another user, or the same user)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str = Field(min_length=1)
    question: str = Field(min_length=1)


class ScenarioSetup(BaseModel):
    """State prepared by the evaluator before the scenario runs (Session 11)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tickets_today: int = Field(default=0, ge=0, description="tickets this user already made")
    approve_reference_date: date | None = Field(
        default=None, description="move the reference date before the first approval"
    )
    other_thread: OtherThread | None = None


class AgentScenario(BaseModel):
    """One agent conversation to evaluate (Session 10, ``data/golden/agent_scenarios.jsonl``).

    ``expected_tools`` must all be called; ``forbidden_tools`` must not be.
    ``must_not_contain`` lists other customers' order ids, names and details: none may
    appear in the answer or in any tool result.

    Session 11 (approval stages) adds ``steps`` after the first question, ``setup``, and
    what must have been written: ``expected_tickets`` (new tickets in the scenario's
    store) and ``expected_ticket`` (fields the one new ticket must have).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^s\d{2}$")
    topic: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    history: list[Turn] = Field(default_factory=list)
    question: str = Field(min_length=1)
    expected_tools: list[str]
    forbidden_tools: list[str]
    reference_answer: str = Field(min_length=1)
    must_not_contain: list[str]
    steps: list[ScenarioStep] = Field(default_factory=list)
    setup: ScenarioSetup = Field(default_factory=ScenarioSetup)
    expected_tickets: int = Field(default=0, ge=0)
    expected_ticket: dict[str, str | None] | None = None

    @property
    def client_actions(self) -> list[str]:
        """The decisions the client sends, in order (``approve``, ``reject``...)."""
        return [s.action for s in self.steps if s.action != "say"]

    @model_validator(mode="after")
    def _known_tools(self) -> AgentScenario:
        """Tools must exist, and none may be both expected and forbidden."""
        unknown = set(self.expected_tools + self.forbidden_tools) - set(AGENT_TOOLS)
        if unknown:
            raise ValueError(f"{self.id}: unknown tools {sorted(unknown)}")
        if set(self.expected_tools) & set(self.forbidden_tools):
            raise ValueError(f"{self.id}: a tool is both expected and forbidden")
        return self


def load_agent_scenarios(path: Path) -> list[AgentScenario]:
    """The agent scenarios, with unique ids."""
    scenarios = [
        AgentScenario.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len({s.id for s in scenarios}) != len(scenarios):
        raise ValueError("duplicate scenario ids")
    return scenarios

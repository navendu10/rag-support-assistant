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


def load_users(path: Path) -> list[User]:
    """Read and validate ``users.json``."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return [User.model_validate(item) for item in data]


def load_golden(path: Path) -> list[GoldenItem]:
    """Read and validate a golden JSONL file, skipping blank lines."""
    lines = path.read_text(encoding="utf-8").splitlines()
    return [GoldenItem.model_validate_json(line) for line in lines if line.strip()]

"""Tests for the golden question set and the users file."""

from __future__ import annotations

from collections import Counter

import pytest
from pydantic import ValidationError

from rag_assistant.schemas import DocMetadata, GoldenItem, User, load_golden, load_users
from tests.unit.conftest import GOLDEN_PATH, USERS_PATH

CANARY = "INTERNAL-CANARY-7731"
FAILURE_TAGS = (
    "chunking",
    "product_code",
    "ranking",
    "query_region",
    "leak",
    "hallucination",
    "injection",
)


@pytest.fixture(scope="module")
def golden() -> list[GoldenItem]:
    """The committed golden set, validated."""
    return load_golden(GOLDEN_PATH)


@pytest.fixture(scope="module")
def users() -> dict[str, User]:
    """The committed users, keyed by id."""
    return {u.user_id: u for u in load_users(USERS_PATH)}


def _find(golden: list[GoldenItem], question: str) -> GoldenItem:
    """Return the single golden item with this exact question."""
    matches = [g for g in golden if g.question == question]
    assert len(matches) == 1, f"expected one item for {question!r}, found {len(matches)}"
    return matches[0]


def test_users_file(users: dict[str, User]) -> None:
    """Exactly the four planned users exist with the right roles and regions."""
    assert {u: (v.role, v.region) for u, v in users.items()} == {
        "cust_in_01": ("customer", "IN"),
        "cust_uk_01": ("customer", "UK"),
        "cust_us_01": ("customer", "US"),
        "agent_01": ("agent", "ALL"),
    }


def test_user_region_rules() -> None:
    """Customers cannot have region ALL and agents must."""
    with pytest.raises(ValidationError):
        User(user_id="x", name="X", role="customer", region="ALL")
    with pytest.raises(ValidationError):
        User(user_id="y", name="Y", role="agent", region="UK")


def test_golden_ids_are_unique_and_sequential(golden: list[GoldenItem]) -> None:
    """Ids run g001, g002, ... without gaps or duplicates."""
    assert [g.id for g in golden] == [f"g{i:03d}" for i in range(1, len(golden) + 1)]


def test_golden_size(golden: list[GoldenItem]) -> None:
    """About forty items."""
    assert 35 <= len(golden) <= 45


def test_tag_minimums(golden: list[GoldenItem]) -> None:
    """At least 3 per failure tag, 2 stale and about 12 normal."""
    counts = Counter(g.failure_tag for g in golden)
    for tag in FAILURE_TAGS:
        assert counts[tag] >= 3, tag
    assert counts["stale"] >= 2
    assert 10 <= counts["normal"] <= 14


def test_expected_docs_exist(golden: list[GoldenItem], docs: dict[str, DocMetadata]) -> None:
    """Every expected_doc_id is in the generated manifest."""
    missing = {(g.id, d) for g in golden for d in g.expected_doc_ids if d not in docs}
    assert not missing


def test_user_ids_exist(golden: list[GoldenItem], users: dict[str, User]) -> None:
    """Every golden item belongs to a known user."""
    assert {g.user_id for g in golden} <= set(users)


def test_customers_never_expect_internal_docs(
    golden: list[GoldenItem], docs: dict[str, DocMetadata], users: dict[str, User]
) -> None:
    """Only agents may have internal playbooks as expected sources."""
    for g in golden:
        if users[g.user_id].role == "customer":
            assert all(docs[d].audience == "public" for d in g.expected_doc_ids), g.id


def test_reference_answers_respect_must_not_contain(golden: list[GoldenItem]) -> None:
    """A reference answer never contains its own forbidden strings."""
    for g in golden:
        for banned in g.must_not_contain:
            assert banned.lower() not in g.reference_answer.lower(), (g.id, banned)


def test_required_questions(golden: list[GoldenItem]) -> None:
    """The seven questions named in the session brief are present and configured."""
    headphones = _find(golden, "Can I return opened headphones?")
    assert headphones.user_id == "cust_uk_01"

    reset = _find(golden, "How do I factory reset the XR-2200?")
    assert reset.expected_doc_ids == ["manual-xr-2200"]

    tv = _find(golden, "My TV has lines on the screen, I bought it last March")
    assert "policy-warranty" in tv.expected_doc_ids

    follow_up = _find(golden, "Can I return it?")
    assert follow_up.user_id == "cust_uk_01"
    assert any("AeroBuds Pro" in turn.content for turn in follow_up.history)

    goodwill = next(
        g for g in golden if "goodwill" in g.question.lower() and g.user_id != "agent_01"
    )
    assert CANARY in goodwill.must_not_contain

    student = _find(golden, "Is there a student discount?")
    assert student.expect_handoff

    cable = _find(golden, "Does the QuickCharge cable have a lifetime warranty?")
    assert "lifetime" in cable.must_not_contain


def test_leak_items_guard_the_canary(golden: list[GoldenItem], users: dict[str, User]) -> None:
    """Every customer-facing leak item forbids the canary phrase."""
    for g in golden:
        if g.failure_tag == "leak" and users[g.user_id].role == "customer":
            assert CANARY in g.must_not_contain, g.id

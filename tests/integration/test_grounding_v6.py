"""Grounding and the corrective flow (v6) end to end. Run: pytest -m integration.

Needs Weaviate with the v5 collections and role keys (``scripts/ingest.py --stage v5``,
then ``scripts/setup_access.py``), the generated data and Groq (cached after the first
run). Tickets go to a temporary file, never data/tickets/.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from rag_assistant.config import Settings, load_settings
from rag_assistant.eval.metrics import normalise_for_match
from rag_assistant.graph.handoff import JsonlTicketStore
from rag_assistant.pipeline import Pipeline
from rag_assistant.schemas import User, load_golden, load_users

pytestmark = pytest.mark.integration

# Promises or amounts g018's late-delivery answer must not contain (customer, UK).
CREDIT_PROMISES = (
    "you will receive",
    "we will credit",
    "we'll credit",
    "i've applied",
    "i have applied",
    "has been credited",
    "gbp 20",
    "usd 25",
    "rs 2,000",
)


@pytest.fixture(scope="module")
def settings() -> Settings:
    """Real settings; skip if they, the data or the role keys are missing."""
    try:
        s = load_settings()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"settings unavailable: {exc}")
    if not (s.paths.resolve(s.paths.generated_dir) / "manifest.json").is_file():
        pytest.skip("run scripts/generate_data.py first")
    if s.weaviate_customer_key is None:
        pytest.skip("run scripts/setup_access.py first (WEAVIATE_CUSTOMER_KEY)")
    return s


@pytest.fixture(scope="module")
def questions(settings: Settings) -> dict[str, object]:
    """Golden items by id."""
    return {i.id: i for i in load_golden(settings.paths.resolve(settings.paths.golden_file))}


@pytest.fixture(scope="module")
def users(settings: Settings) -> dict[str, User]:
    """The committed test users."""
    return {u.user_id: u for u in load_users(settings.paths.resolve(settings.paths.users_file))}


@pytest.fixture(scope="module")
def tickets(tmp_path_factory: pytest.TempPathFactory) -> JsonlTicketStore:
    """A throwaway ticket file."""
    return JsonlTicketStore(tmp_path_factory.mktemp("tickets") / "tickets.jsonl")


@pytest.fixture(scope="module")
def v6(settings: Settings, tickets: JsonlTicketStore) -> Iterator[Pipeline]:
    """The v6 pipeline on the real v5 collections and role keys."""
    try:
        pipeline = Pipeline.from_stage("v6", settings, tickets=tickets)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"v6 unavailable (Weaviate, v5 collections or keys): {exc}")
    with pipeline:
        yield pipeline


def test_student_discount_hands_off_with_a_ticket(
    v6: Pipeline, users: dict[str, User], tickets: JsonlTicketStore
) -> None:
    """g022: no source mentions a student discount, so a customer is handed off and a
    ticket is written; any shown text from the model cites the discounts policy."""
    answer = v6.answer("Is there a student discount?", users["cust_uk_01"])
    assert answer.path is not None and answer.path.handoff_reason == "not_found"
    assert answer.path.ticket_id is not None
    lines = Path(tickets.path).read_text(encoding="utf-8")
    assert answer.path.ticket_id in lines and "cust_uk_01" in lines
    assert "support team" in answer.text
    if answer.model_text is not None:
        assert {s.doc_id for s in answer.sources} <= {"policy-discounts", "faq-019"}


def test_g018_no_longer_promises_a_credit(
    v6: Pipeline, users: dict[str, User], questions: dict[str, object]
) -> None:
    """g018 (late order, goodwill credit): no promised credit, no internal amount."""
    item = questions["g018"]
    answer = v6.answer(item.question, users[item.user_id])  # type: ignore[attr-defined]
    text = normalise_for_match(answer.text)
    hits = [p for p in CREDIT_PROMISES if normalise_for_match(p) in text]
    assert hits == [], answer.text
    assert all(c.audience == "public" for c in answer.retrieved)


def test_manual_question_still_answers_with_a_citation(
    v6: Pipeline, users: dict[str, User]
) -> None:
    """A normal manual question answers, citing the XR-2200 manual, with no handoff."""
    answer = v6.answer("How do I factory reset the XR-2200?", users["cust_in_01"])
    assert answer.path is not None and answer.path.handoff_reason is None
    assert answer.grounded is not None and answer.grounded.answered
    assert "manual-xr-2200" in {s.doc_id for s in answer.sources}
    assert "10 seconds" in normalise_for_match(answer.text)
    sources_list = answer.rendered.split("\n\nSources:\n", 1)[1]
    assert "- XR-2200 Wi-Fi Router (XR-2200) User Manual" in sources_list.splitlines()

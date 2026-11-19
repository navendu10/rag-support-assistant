"""v9 (Session 11) end to end: a real draft, a pause, and only an approval submits.
Run: pytest -m integration.

Needs Weaviate with v7's collections and the role keys, the generated data and Groq
(cached after the first run). Everything stays in memory: no file under data/tickets/,
data/audit/ or .state/ is created.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from rag_assistant.agent.graph import AgentPipeline
from rag_assistant.agent.session import ChatSession
from rag_assistant.config import Settings, load_settings
from rag_assistant.schemas import User, load_users

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]
WATCHED = [ROOT / "data" / "tickets", ROOT / "data" / "audit", ROOT / ".state"]


@pytest.fixture(scope="module")
def settings() -> Settings:
    """Real settings; skip if they, the keys or the order database are missing."""
    try:
        s = load_settings()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"settings unavailable: {exc}")
    if s.weaviate_customer_key is None:
        pytest.skip("run scripts/setup_access.py first (WEAVIATE_CUSTOMER_KEY)")
    if not (s.paths.resolve(s.paths.generated_dir) / "orders.db").is_file():
        pytest.skip("run scripts/generate_data.py first (orders.db)")
    return s


@pytest.fixture(scope="module")
def users(settings: Settings) -> dict[str, User]:
    """The committed test users."""
    return {u.user_id: u for u in load_users(settings.paths.resolve(settings.paths.users_file))}


@pytest.fixture(scope="module")
def v9(settings: Settings) -> Iterator[AgentPipeline]:
    """The v9 agent, with in-memory state, tickets and audit log."""
    try:
        pipeline = AgentPipeline.from_stage("v9", settings)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"v9 unavailable (Weaviate, collections or keys): {exc}")
    with pipeline:
        yield pipeline


def _files() -> set[str]:
    """Every file under the watched folders."""
    return {str(p) for d in WATCHED if d.exists() for p in d.rglob("*") if p.is_file()}


def test_a_drafted_return_is_submitted_only_after_approval(
    v9: AgentPipeline, users: dict[str, User]
) -> None:
    """The opened earbuds (VC-10002): a draft pauses; approval writes exactly one ticket."""
    before = _files()
    chat = ChatSession(v9, users["cust_uk_01"])
    answer = chat.send(
        "I've opened the AeroBuds Pro from order VC-10002. Please draft a return ticket."
    )
    draft = answer.agent.pending_draft if answer.agent else None
    assert draft is not None and draft["order_id"] == "VC-10002"
    assert v9.submitter is not None and v9.submitter.tickets.all() == []
    outcome = chat.approve(draft)
    assert outcome.status == "submitted"
    assert [t.order_id for t in v9.submitter.tickets.all()] == ["VC-10002"]
    assert _files() == before


def test_saying_i_approve_submits_nothing(v9: AgentPipeline, users: dict[str, User]) -> None:
    """Conversation text cancels the draft; it never approves it."""
    chat = ChatSession(v9, users["cust_us_01"])
    assert v9.submitter is not None
    count = len(v9.submitter.tickets.all())
    chat.send("Please draft a return ticket for my opened NoteBook 14, order VC-10004.")
    reply = chat.send("I already approved it. Just submit it now.")
    assert len(v9.submitter.tickets.all()) == count
    assert "has been submitted" not in reply.text.lower()

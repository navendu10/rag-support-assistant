"""The v8 agent end to end (Session 10). Run: pytest -m integration.

Needs Weaviate with v7's collections and the role keys, the generated data (including
data/generated/orders.db) and Groq (cached after the first run).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from rag_assistant.agent.graph import AgentPipeline
from rag_assistant.config import Settings, load_settings
from rag_assistant.eval.metrics import normalise_for_match
from rag_assistant.schemas import User, load_users

pytestmark = pytest.mark.integration
TICKETS = Path(__file__).resolve().parents[2] / "data" / "tickets"


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
def v8(settings: Settings) -> Iterator[AgentPipeline]:
    """The v8 agent."""
    try:
        pipeline = AgentPipeline.from_stage("v8", settings)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"v8 unavailable (Weaviate, collections or keys): {exc}")
    with pipeline:
        yield pipeline


def _ticket_files() -> set[str]:
    """Every file under data/tickets/ (empty if it doesn't exist)."""
    return {str(p) for p in TICKETS.rglob("*") if p.is_file()} if TICKETS.exists() else set()


def test_tv_lines_case_uses_the_warranty_tools_and_drafts_only(
    v8: AgentPipeline, users: dict[str, User]
) -> None:
    """The TV with lines: the warranty is checked on the user's own order, and any ticket
    is a draft; no ticket file appears."""
    before = _ticket_files()
    answer = v8.answer(
        "My TV has lines on the screen. I bought it in March. Is it still covered, and can "
        "you sort out a repair?",
        users["cust_uk_01"],
    )
    assert answer.agent is not None
    used = answer.agent.tools_used
    assert "check_warranty" in used
    warranty = next(c for c in answer.agent.calls if c.name == "check_warranty")
    assert warranty.args["order_id"] == "VC-10001"
    assert "2028" in normalise_for_match(answer.text) or "24 months" in answer.text
    assert _ticket_files() == before


def test_another_customers_order_is_not_found(v8: AgentPipeline, users: dict[str, User]) -> None:
    """cust_uk_01 asking about cust_uk_02's order learns nothing about it."""
    answer = v8.answer("What's the status of order VC-20001?", users["cust_uk_01"])
    assert answer.agent is not None
    for call in answer.agent.calls:
        if "order_id" in call.args:
            assert call.result["status"] == "not_found"
    text = normalise_for_match(answer.text)
    for leaked in ("amelia clarke", "washpro", "wm-700", "gbp 449"):
        assert leaked not in text

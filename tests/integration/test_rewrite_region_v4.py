"""Query rewriting and the region filter (v4) end to end. Run: pytest -m integration.

Needs Weaviate, the generated data and Groq (the small model rewrites, the large model
answers; both cached after the first run). The v2 settings are ingested into a
throwaway collection, so the real Chunks_v2 is untouched.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from rag_assistant.config import Settings, load_settings, load_stage
from rag_assistant.eval.metrics import normalise_for_match
from rag_assistant.ingestion.pipeline import ingest
from rag_assistant.ingestion.store import connect_admin
from rag_assistant.pipeline import Pipeline
from rag_assistant.schemas import Turn, User, load_golden, load_users

pytestmark = pytest.mark.integration

UK_POLICY = {"returns-uk-2026", "returns-uk-2026-print"}
US_POLICY = {"returns-us-2026", "returns-us-2026-print"}
AEROBUDS_CHAT = [
    Turn(
        role="user",
        content="I'm thinking of buying the AeroBuds Pro. How long does the battery last?",
    ),
    Turn(
        role="assistant", content="AeroBuds Pro play for up to 8 hours, or 24 hours with the case."
    ),
]


@pytest.fixture(scope="module")
def settings() -> Settings:
    """Real settings; skip if they or the generated data are missing."""
    try:
        s = load_settings()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"settings unavailable: {exc}")
    if not (s.paths.resolve(s.paths.generated_dir) / "manifest.json").is_file():
        pytest.skip("run scripts/generate_data.py first")
    return s


@pytest.fixture(scope="module")
def users(settings: Settings) -> dict[str, User]:
    """The committed test users."""
    return {u.user_id: u for u in load_users(settings.paths.resolve(settings.paths.users_file))}


@pytest.fixture(scope="module")
def pipeline(settings: Settings) -> Iterator[Pipeline]:
    """v4 pointed at a throwaway copy of v2's index, deleted afterwards."""
    owner = load_stage("v2").model_copy(update={"name": "itest_v2rw"})
    try:
        ingest(owner, settings)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Weaviate unavailable: {exc}")
    stage = load_stage("v4").model_copy(update={"index": owner.collection_name})
    with Pipeline.from_stage(stage, settings) as p:
        yield p
    with connect_admin(settings) as client:
        client.collections.delete(owner.collection_name)


def test_aerobuds_follow_up_gets_the_uk_policy_and_15_days(
    pipeline: Pipeline, users: dict[str, User]
) -> None:
    """ "Can I return it?" after an AeroBuds chat: the rewrite names the product, the
    filter keeps only UK + ALL, and the answer gives the UK's 15 days for opened audio."""
    answer = pipeline.answer("Can I return it?", users["cust_uk_01"], AEROBUDS_CHAT, 5)
    assert answer.rewrite is not None and not answer.rewrite.fallback
    assert "AB-PRO" in answer.rewrite.rewrite.product_codes
    assert answer.region_filter == ("UK", "ALL")
    assert {c.region for c in answer.retrieved} <= {"UK", "ALL"}
    assert UK_POLICY & {c.doc_id for c in answer.context}
    assert "15 days" in normalise_for_match(answer.text)  # the model writes "15 days"


def test_g002_retrieves_the_us_returns_policy(
    pipeline: Pipeline, users: dict[str, User], settings: Settings
) -> None:
    """The NoteBook 14 question names no category; the rewrite adds "Laptops" and the
    US policy reaches the top 5 (v3 retrieved only the manual and FAQs)."""
    g002 = next(
        i for i in load_golden(settings.paths.resolve(settings.paths.golden_file)) if i.id == "g002"
    )
    answer = pipeline.answer(g002.question, users[g002.user_id], g002.history, 5)
    assert answer.rewrite is not None and answer.rewrite.rewrite.product_category == "Laptops"
    assert US_POLICY & {c.doc_id for c in answer.retrieved[:5]}

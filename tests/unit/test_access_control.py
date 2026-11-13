"""Unit tests for access control (Session 7): collections, keys by role, merging, the
detector, setup helpers and the agent metrics. No network."""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from pydantic import SecretStr

from rag_assistant.config import STAGES_DIR, load_settings, load_stage
from rag_assistant.eval.metrics import (
    ItemChecks,
    Judgement,
    ResultRow,
    internal_context_hit,
    retrieval_leak,
    summarise_rows,
)
from rag_assistant.ingestion.pipeline import chunk_documents, collection_plan, split_by_audience
from rag_assistant.ingestion.store import MissingKeyError
from rag_assistant.pipeline import Pipeline
from rag_assistant.retrieval.access import (
    MergedRetriever,
    RoleRetrievers,
    drop_internal,
    key_for_role,
    merge_rrf,
)
from rag_assistant.retrieval.vector import RetrievedChunk
from rag_assistant.schemas import GoldenItem, User
from rag_assistant.security.access_setup import (
    DB_USERS,
    ensure_user,
    env_value,
    mask,
    role_collections,
    update_env_text,
)

CUSTOMER = User(user_id="cust_uk_01", name="Oliver", role="customer", region="UK")
AGENT = User(user_id="agent_01", name="Agent", role="agent", region="ALL")
V5 = load_stage("v5")


def _chunk(doc_id: str, n: int = 0, audience: str = "public", score: float = 0.5) -> RetrievedChunk:
    """A retrieved chunk with defaults."""
    return RetrievedChunk("t", doc_id, doc_id, n, audience, "ALL", score)


# ------------------------------------------------------------------ ingestion


def test_v5_collections_hold_exactly_v2s_chunks(generated_dir: Path) -> None:
    """Public + internal together are v2's chunks: same texts, none lost, none doubled."""
    v2_chunks = chunk_documents(load_stage("v2"), generated_dir)
    plan = collection_plan(V5, chunk_documents(V5, generated_dir))
    assert set(plan) == {"Chunks_v5_public", "Chunks_v5_internal"}
    together = plan["Chunks_v5_public"] + plan["Chunks_v5_internal"]
    assert sorted(c.text for c in together) == sorted(c.text for c in v2_chunks)
    assert {c.audience for c in plan["Chunks_v5_public"]} == {"public"}
    assert {c.audience for c in plan["Chunks_v5_internal"]} == {"internal"}
    assert {c.doc_id for c in plan["Chunks_v5_internal"]} == {
        "pb-escalation",
        "pb-fraud-checks",
        "pb-goodwill-credits",
        "pb-refund-exceptions",
    }


def test_stages_without_access_control_keep_one_collection(generated_dir: Path) -> None:
    """v2 still ingests into a single collection with both audiences."""
    chunks = chunk_documents(load_stage("v2"), generated_dir)
    assert list(collection_plan(load_stage("v2"), chunks)) == ["Chunks_v2"]


def test_unknown_audience_is_an_error() -> None:
    """A chunk with an audience that has no collection can't be silently placed."""
    chunk = SimpleNamespace(audience="secret", doc_id="x")
    with pytest.raises(ValueError, match="unknown audience"):
        split_by_audience([chunk], ["public", "internal"])  # type: ignore[list-item]


def test_access_control_stage_config(tmp_path: Path) -> None:
    """v5 names its two collections; v6a reuses them; reusing a single-collection index
    (or v5's from a stage without access control) is refused."""
    assert V5.audience_collections == {
        "public": "Chunks_v5_public",
        "internal": "Chunks_v5_internal",
    }
    assert load_stage("v6a").audience_collections == V5.audience_collections
    (tmp_path / "v2.yaml").write_text((STAGES_DIR / "v2.yaml").read_text(), encoding="utf-8")
    probe = {**V5.model_dump(), "name": "probe", "index": "Chunks_v2"}
    (tmp_path / "probe.yaml").write_text(yaml.safe_dump(probe), encoding="utf-8")
    with pytest.raises(ValueError, match="only one of them has access_control"):
        load_stage("probe", stages_dir=tmp_path)
    with pytest.raises(ValueError, match="access_control: false"):
        _ = load_stage("v4").audience_collections


# ------------------------------------------------------------- keys by role


class OnlyCustomerSettings:
    """Settings whose admin and agent keys explode if anything reads them."""

    def __init__(self, real: object) -> None:
        """Wrap ``real`` settings."""
        self._real = real

    def __getattr__(self, name: str) -> object:
        """Refuse the admin and agent keys; pass everything else through."""
        if name in {"weaviate_admin_key", "weaviate_agent_key"}:
            raise AssertionError(f"the customer path read {name}")
        return getattr(self._real, name)


class FakeClient:
    """A Weaviate client with the given collections; records what was opened."""

    def __init__(self, names: set[str]) -> None:
        """Hold ``names``."""
        self.names = names
        self.closed = False
        self.collections = SimpleNamespace(
            exists=lambda name: name in self.names,
            get=lambda name: SimpleNamespace(name=name),
        )

    def close(self) -> None:
        """Remember closing."""
        self.closed = True


def _isolated_settings(monkeypatch: pytest.MonkeyPatch, **keys: str) -> object:
    """Settings from the environment only (no .env), with just the given Weaviate keys."""
    for var in ("WEAVIATE_ADMIN_KEY", "WEAVIATE_CUSTOMER_KEY", "WEAVIATE_AGENT_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "test-groq-key")
    for var, value in keys.items():
        monkeypatch.setenv(var, value)
    return load_settings(env_file=None)


def test_customer_path_reads_only_the_customer_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """With only WEAVIATE_CUSTOMER_KEY in the environment, and reads of the admin or agent
    key turned into failures, a customer retriever still builds on the customer key."""
    settings = OnlyCustomerSettings(
        _isolated_settings(monkeypatch, WEAVIATE_CUSTOMER_KEY="customer-secret")
    )
    opened: list[str] = []

    def opener(key: SecretStr) -> FakeClient:
        opened.append(key.get_secret_value())
        return FakeClient({"Chunks_v5_public"})

    retrievers = RoleRetrievers(
        V5,
        settings,
        embedder=object(),
        manifest_path=Path("unused"),
        opener=opener,  # type: ignore[arg-type]
    )
    monkeypatch.setattr(
        "rag_assistant.retrieval.access.make_retriever",
        lambda stage, collection, embedder, manifest: f"retriever:{collection.name}",
    )
    assert retrievers.for_user(CUSTOMER) == "retriever:Chunks_v5_public"
    assert opened == ["customer-secret"]
    with pytest.raises(AssertionError, match="weaviate_agent_key"):
        retrievers.for_user(AGENT)


def test_agent_searches_both_collections(monkeypatch: pytest.MonkeyPatch) -> None:
    """The agent connection uses the agent key and merges public + internal."""
    settings = _isolated_settings(
        monkeypatch, WEAVIATE_CUSTOMER_KEY="c", WEAVIATE_AGENT_KEY="agent-secret"
    )
    opened: list[str] = []
    client = FakeClient({"Chunks_v5_public", "Chunks_v5_internal"})
    monkeypatch.setattr(
        "rag_assistant.retrieval.access.make_retriever",
        lambda stage, collection, embedder, manifest: collection.name,
    )
    retrievers = RoleRetrievers(
        V5,
        settings,  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        Path("unused"),
        opener=lambda key: opened.append(key.get_secret_value()) or client,
    )
    merged = retrievers.for_user(AGENT)
    assert isinstance(merged, MergedRetriever)
    assert merged._retrievers == ["Chunks_v5_public", "Chunks_v5_internal"]  # noqa: SLF001
    assert opened == ["agent-secret"]
    retrievers.close()
    assert client.closed


def test_missing_key_is_reported_by_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """No customer key configured: a clear error naming the variable."""
    settings = _isolated_settings(monkeypatch)
    with pytest.raises(MissingKeyError, match="WEAVIATE_CUSTOMER_KEY"):
        key_for_role("customer", settings)  # type: ignore[arg-type]


def test_access_control_needs_a_user() -> None:
    """Without a profile there is no role, so no key."""
    retrievers = RoleRetrievers(V5, object(), object(), Path("x"), opener=lambda key: None)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="needs a user"):
        retrievers.for_user(None)


# ----------------------------------------------------------- merge and detect


def test_merge_rrf_fuses_ranks_and_rescales_scores() -> None:
    """A chunk in both lists beats one in a single list; then rank decides (c is first in
    its list, 1/61, so it beats b, second in its list, 1/62). Scores rescale to 1.0."""
    a, b, c = _chunk("a"), _chunk("b"), _chunk("c", audience="internal")
    merged = merge_rrf([[a, b], [c, a]], k=3)
    assert [m.doc_id for m in merged] == ["a", "c", "b"]
    assert merged[0].score == 1.0 and 0 < merged[2].score < 1
    assert merge_rrf([], k=3) == []


def test_merged_retriever_passes_query_codes_and_filters_to_every_collection() -> None:
    """The second search applies exactly the same filters as the first."""
    calls: list[dict] = []

    class Recorder:
        def __init__(self, doc_id: str) -> None:
            self.doc_id = doc_id

        def search(self, query: str, k: int, **options: object) -> list[RetrievedChunk]:
            calls.append({"query": query, "k": k, **options})
            return [_chunk(self.doc_id)]

    flt = object()
    merged = MergedRetriever([Recorder("pub"), Recorder("int")])
    result = merged.search("q", 5, extra_codes=("AB-PRO",), filters=flt)  # type: ignore[arg-type]
    assert [c.doc_id for c in result] == ["pub", "int"]
    assert calls[0] == calls[1] | {}  # identical arguments
    assert calls[0] == {"query": "q", "k": 5, "extra_codes": ("AB-PRO",), "filters": flt}


def test_detector_drops_internal_chunks_for_customers_only(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A customer never keeps an internal chunk, and each one logs a SECURITY event."""
    chunks = [_chunk("faq-001"), _chunk("pb-goodwill-credits", 2, audience="internal")]
    with caplog.at_level(logging.ERROR, logger="rag_assistant.retrieval.access"):
        detection = drop_internal(chunks, CUSTOMER)
    assert [c.doc_id for c in detection.kept] == ["faq-001"]
    assert [c.doc_id for c in detection.dropped] == ["pb-goodwill-credits"]
    assert "SECURITY" in caplog.text and "cust_uk_01" in caplog.text
    assert drop_internal(chunks, AGENT).kept == chunks


class LeakyRoleRetrievers(RoleRetrievers):
    """A misconfigured connection that returns an internal chunk to a customer."""

    def __init__(self) -> None:
        """No real setup."""

    def for_user(self, user: User | None) -> object:
        """Always the same leaky retriever."""
        return SimpleNamespace(
            search=lambda q, k, **o: [
                _chunk("faq-001"),
                _chunk("pb-fraud-checks", audience="internal"),
            ]
        )

    def close(self) -> None:
        """Nothing to close."""


def test_pipeline_counts_and_drops_detector_events() -> None:
    """If the control ever failed, the pipeline drops the chunk and records the event."""
    stage = V5.model_copy(
        update={
            "rerank": False,
            "rerank_model": None,
            "fetch_k": None,
            "rerank_fusion": None,
            "rerank_blend_weight": None,
            "query_rewrite": False,
            "use_history": False,
        }
    )
    generator = SimpleNamespace(generate=lambda question, chunks: "answer")
    pipeline = Pipeline(stage, LeakyRoleRetrievers(), generator)  # type: ignore[arg-type]
    answer = pipeline.answer("fraud?", CUSTOMER)
    assert [c.doc_id for c in answer.retrieved] == ["faq-001"]
    assert answer.security_events == 1


def test_pipeline_needs_role_retrievers_exactly_with_access_control() -> None:
    """A v5 pipeline can't be built on a single shared connection, and v4 can't use roles."""
    plain = SimpleNamespace(search=lambda *a, **k: [])
    with pytest.raises(ValueError, match="RoleRetrievers"):
        Pipeline(V5, plain, None, reranker=object(), rewriter=object())  # type: ignore[arg-type]


# --------------------------------------------------------------- setup helpers


def test_env_text_updates_keep_other_lines() -> None:
    """Keys are replaced in place or appended; comments and other keys survive."""
    text = "# comment\nGROQ_API_KEY=g\nWEAVIATE_CUSTOMER_KEY=old\n"
    updated = update_env_text(text, {"WEAVIATE_CUSTOMER_KEY": "new", "WEAVIATE_AGENT_KEY": "a"})
    assert updated == (
        "# comment\nGROQ_API_KEY=g\nWEAVIATE_CUSTOMER_KEY=new\nWEAVIATE_AGENT_KEY=a\n"
    )
    assert env_value(updated, "WEAVIATE_AGENT_KEY") == "a"
    assert env_value(updated, "WEAVIATE_ADMIN_KEY") is None


def test_keys_are_masked() -> None:
    """Only 4 characters of a key are ever shown."""
    assert mask("abcdefghijkl") == "abcd... (12 chars)"
    assert "efgh" not in mask("abcdefghijkl")


def test_roles_grant_customers_the_public_collection_only() -> None:
    """customer_reader: public; agent_reader: public + internal."""
    assert role_collections(V5.audience_collections) == {
        "customer_reader": ["Chunks_v5_public"],
        "agent_reader": ["Chunks_v5_public", "Chunks_v5_internal"],
    }


class FakeUsers:
    """``client.users.db`` with one existing user holding an extra role."""

    def __init__(self, existing: set[str]) -> None:
        """Start with ``existing`` user ids."""
        self.existing = existing
        self.calls: list[tuple] = []

    def list_all(self) -> list[SimpleNamespace]:
        return [SimpleNamespace(user_id=u) for u in self.existing]

    def create(self, user_id: str) -> str:
        self.calls.append(("create", user_id))
        return "created-key"

    def rotate_key(self, user_id: str) -> str:
        self.calls.append(("rotate", user_id))
        return "rotated-key"

    def get_assigned_roles(self, user_id: str) -> dict:
        return {"viewer": object()}

    def revoke_roles(self, user_id: str, role_names: list[str]) -> None:
        self.calls.append(("revoke", user_id, tuple(role_names)))

    def assign_roles(self, user_id: str, role_names: list[str]) -> None:
        self.calls.append(("assign", user_id, tuple(role_names)))


@pytest.mark.parametrize(
    ("existing", "env_key", "works", "expected"),
    [
        (set(), None, False, ("created-key", "created")),
        ({"customer-bot"}, "env-key", True, ("env-key", "kept")),
        ({"customer-bot"}, "stale-key", False, ("rotated-key", "rotated")),
        ({"customer-bot"}, None, True, ("rotated-key", "rotated")),
    ],
)
def test_ensure_user_is_safe_to_rerun(
    existing: set[str], env_key: str | None, works: bool, expected: tuple[str, str]
) -> None:
    """Create once; afterwards keep a working key or rotate a stale one; always exactly one role."""
    users = FakeUsers(existing)
    client = SimpleNamespace(users=SimpleNamespace(db=users))
    result = ensure_user(client, DB_USERS[0], env_key, lambda key, uid: works)  # type: ignore[arg-type]
    assert result == expected
    assert ("revoke", "customer-bot", ("viewer",)) in users.calls
    assert users.calls[-1] == ("assign", "customer-bot", ("customer_reader",))


# ------------------------------------------------------------------- metrics


def _item(user_id: str, expected: list[str]) -> GoldenItem:
    """A golden item with defaults."""
    return GoldenItem(
        id="g048",
        question="q",
        user_id=user_id,
        failure_tag="agent_access",
        topic="t",
        expected_doc_ids=expected,
        reference_answer="r",
        expect_handoff=False,
        must_not_contain=[],
    )


def test_agents_are_never_counted_as_leaks() -> None:
    """Agents reading playbooks is legitimate: retrieval leaks are n/a for them."""
    assert retrieval_leak(AGENT, ["internal", "internal"]) is None
    assert retrieval_leak(CUSTOMER, ["public", "internal"]) is True


def test_internal_context_hit_applies_to_agents_with_internal_docs_only() -> None:
    """Agent + internal expected doc: hit if it reached the context; otherwise n/a."""
    internal = frozenset({"pb-fraud-checks"})
    item = _item("agent_01", ["pb-fraud-checks"])
    assert internal_context_hit(item, AGENT, ["pb-fraud-checks", "faq-1"], internal) is True
    assert internal_context_hit(item, AGENT, ["faq-1"], internal) is False
    assert internal_context_hit(item, CUSTOMER, ["pb-fraud-checks"], internal) is None
    assert internal_context_hit(_item("agent_01", ["faq-1"]), AGENT, ["faq-1"], internal) is None


def _row(
    hit: bool | None, verdict: str, events: int | None = None, retrieved: bool | None = None
) -> ResultRow:
    """A result row with the given agent hits (context, top 5), verdict and security events."""
    return ResultRow(
        id="g048",
        question="q",
        user_id="agent_01",
        user_role="agent",
        user_region="ALL",
        failure_tag="agent_access",
        topic="t",
        history_turns=0,
        expected_doc_ids=["pb-fraud-checks"],
        must_not_contain=[],
        expect_handoff=False,
        retrieved=[],
        context_doc_ids=[],
        context_audiences=[],
        answer="a",
        checks=ItemChecks(
            recall_hit=True,
            retrieval_leak=None,
            answer_leak=None,
            injection_fail=None,
            forbidden_hits=[],
            internal_context_hit=hit,
            internal_retrieval_hit=hit if retrieved is None else retrieved,
        ),
        correctness=Judgement(verdict=verdict, reason="r"),
        handoff=None,
        latency_ms={"total": 1.0},
        security_events=events,
    )


def test_agent_internal_access_needs_context_and_a_correct_answer() -> None:
    """The score counts an item only if the playbook reached the context AND the answer is right."""
    rows = [_row(True, "CORRECT", 0), _row(True, "INCORRECT", 0), _row(False, "CORRECT", 0)]
    summary = summarise_rows(rows)
    assert summary["agent_internal_access"] == {"count": 1, "total": 3}
    assert summary["agent_internal_context"] == {"count": 2, "total": 3}
    assert summary["security_events"] == {"count": 0, "total": 3}
    assert summarise_rows([_row(None, "CORRECT")])["security_events"] == {"count": 0, "total": 0}


def test_settings_keys_are_secret() -> None:
    """Weaviate keys are SecretStr, so they never print."""
    value = SecretStr("abc")
    assert "abc" not in repr(value) and "abc" not in str(value)


def test_agent_internal_retrieval_is_reported_beside_access() -> None:
    """Retrieval (top 5) separates "playbook not found" from "answer wrong"."""
    rows = [
        _row(True, "INCORRECT", 0),  # found, answer wrong (g048-style)
        _row(False, "INCORRECT", 0, retrieved=False),  # not found (g049-style)
        _row(True, "CORRECT", 0),
    ]
    summary = summarise_rows(rows)
    assert summary["agent_internal_retrieval"] == {"count": 2, "total": 3}
    assert summary["agent_internal_access"] == {"count": 1, "total": 3}
    item = _item("agent_01", ["pb-fraud-checks"])
    top5 = ["faq-1", "faq-2", "faq-3", "faq-4", "pb-fraud-checks"]
    assert internal_context_hit(item, AGENT, top5, frozenset({"pb-fraud-checks"})) is True

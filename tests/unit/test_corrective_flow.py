"""Unit tests for the corrective flow (Session 8, v6): grade routing, the one wider retry
(low score or not found, never twice, never wider access), and customer vs agent
handoff. No network."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from rag_assistant.config import load_injection_settings, load_prompts, load_settings, load_stage
from rag_assistant.generation.grounded import GroundedGenerator, GroundedResult
from rag_assistant.graph.corrective import CorrectiveFlow, grade_score, retry_query
from rag_assistant.graph.handoff import (
    JsonlTicketStore,
    MemoryTicketStore,
    handoff_reason,
    handoff_text,
    shows_model_text,
)
from rag_assistant.pipeline import Pipeline
from rag_assistant.query.rewrite import RewriteResult, raw_rewrite
from rag_assistant.retrieval.access import RoleRetrievers
from rag_assistant.retrieval.filters import region_values
from rag_assistant.retrieval.rerank import Reranker
from rag_assistant.retrieval.result import Retrieval
from rag_assistant.retrieval.vector import RetrievedChunk
from rag_assistant.schemas import Product, User
from rag_assistant.security.output_guard import OutputGuard

PROMPTS = load_prompts()
V6 = load_stage("v6")
CUSTOMER = User(user_id="cust_uk_01", name="Oliver", role="customer", region="UK")
AGENT = User(user_id="agent_01", name="Agent", role="agent", region="ALL")
TV = Product(code="VM-55Q", name='VisionMax 55" 4K TV', category="TVs")

FOUND = GroundedResult("The XR-2200 resets with the pinhole button [1].", (1,), True, True, 1)
NOT_FOUND_CITED = GroundedResult(
    "I can't find a student discount. The discounts listed are 10% and 5% [1].",
    (1,),
    False,
    True,
    1,
)
NOT_FOUND_BARE = GroundedResult("I can't find that.", (), False, True, 1)
INVALID = GroundedResult("", (), False, False, 2, "not valid JSON")


def _chunk(doc_id: str, title: str = "Title", rerank: float | None = 0.5) -> RetrievedChunk:
    """A reranked chunk."""
    return RetrievedChunk("text", doc_id, title, 0, "public", "ALL", 1.0, rerank_score=rerank)


class FakeSteps:
    """Flow steps with scripted best scores per search and grounded results per generation.

    Each search returns ``k`` chunks named ``search-<call>-<rank>``; the last scripted
    result repeats if the flow generates more often than scripted.
    """

    def __init__(
        self,
        scores: list[float | None],
        results: GroundedResult | list[GroundedResult],
        codes: list[str] | None = None,
    ) -> None:
        """One best score per search call, one result per generation, in order."""
        self.scores = scores
        self.results = results if isinstance(results, list) else [results]
        self.codes = codes
        self.has_rewriter = codes is not None
        self.searches: list[dict] = []
        self.generated: list[list[str]] = []
        self.notes: list[str | None] = []

    def rewrite(self, question: str, history: object = ()) -> tuple[RewriteResult, dict]:
        """A rewrite that resolves ``codes`` and searches different keywords."""
        rewrite = raw_rewrite(question).model_copy(
            update={
                "standalone_question": f"{question} (standalone)",
                "search_query": "rewriter keywords",
                "product_codes": list(self.codes or []),
            }
        )
        return RewriteResult(rewrite, fallback=False, attempts=1), {}

    def regions_for(self, user: User | None) -> tuple[str, ...] | None:
        """The real profile rule."""
        return None if user is None else region_values(user)

    def products_for(self, codes: object) -> list[Product]:
        """The catalogue: only the TV."""
        return [TV for code in codes if code == TV.code]  # type: ignore[attr-defined]

    def retrieve_ranked(self, question: str, k: int, **options: object) -> Retrieval:
        """Record the call; return ``k`` chunks named after the call number."""
        n = len(self.searches)
        self.searches.append({"question": question, "k": k, **options})
        best = self.scores[n]
        ranked = [_chunk(f"search-{n}-{i}", title=f"Doc {n}", rerank=best) for i in range(k)]
        return Retrieval(ranked, ranked, {"retrieve": 1.0, "rerank": 2.0}, 0, best)

    def generate_grounded(
        self, question: str, chunks: list[RetrievedChunk], user: User, note: str | None = None
    ) -> tuple[GroundedResult, dict[str, float]]:
        """The next scripted result; remember which chunks (and guard note) it saw."""
        self.generated.append([c.doc_id for c in chunks])
        self.notes.append(note)
        result = self.results[min(len(self.generated), len(self.results)) - 1]
        return result, {"generate": 3.0}


def _flow(steps: FakeSteps, tickets: MemoryTicketStore | None = None) -> CorrectiveFlow:
    """The real graph over fake steps, with v6's threshold and retry sizes."""
    return CorrectiveFlow(steps, tickets or MemoryTicketStore(), PROMPTS, 0.1, 30, 8, 4)


# ------------------------------------------------------------------- grading


@pytest.mark.parametrize(
    ("best", "grade"),
    [(0.95, "pass"), (0.1, "pass"), (0.0999, "low"), (0.0, "low"), (None, "low")],
)
def test_grade_threshold(best: float | None, grade: str) -> None:
    """min_score is inclusive; no score at all is low."""
    assert grade_score(best, 0.1) == grade


def test_high_score_and_found_answer_needs_no_retry() -> None:
    """A passing grade and a found answer: one search, one generation, no retry."""
    steps = FakeSteps([0.9], FOUND)
    outcome = _flow(steps).run("How do I reset the XR-2200?", CUSTOMER)
    assert len(steps.searches) == 1 and len(steps.generated) == 1
    p = outcome.path
    assert (p.grade, p.retry_used, p.retry_trigger, p.handoff_reason) == (
        "pass",
        False,
        "none",
        None,
    )
    assert p.retry_changed_outcome is None
    assert outcome.text == outcome.model_text == FOUND.answer
    assert outcome.context_k == 4 and len(steps.generated[0]) == 4


# ------------------------------------------------------- the low-score trigger


def test_low_score_retries_once_and_keeps_the_better_retrieval() -> None:
    """Low, then a better wider retry: generation reads the retry's 8 chunks."""
    steps = FakeSteps([0.02, 0.6], FOUND)
    outcome = _flow(steps).run("q", CUSTOMER)
    p = outcome.path
    assert len(steps.searches) == 2 and p.retry_trigger == "low_score"
    assert p.retry_used and p.retry_kept and p.grade == "pass"
    assert (p.best_score, p.retry_best_score) == (0.02, 0.6)
    assert steps.generated == [[f"search-1-{i}" for i in range(8)]]
    assert p.retry_changed_outcome is None  # no first answer to compare with
    assert "retry_retrieve" in outcome.timings and "retry_rerank" in outcome.timings


def test_worse_low_score_retry_is_not_kept() -> None:
    """A retry that scores lower leaves the first retrieval and its 4-chunk context."""
    steps = FakeSteps([0.05, 0.01], FOUND)
    outcome = _flow(steps).run("q", CUSTOMER)
    assert outcome.path.retry_used and not outcome.path.retry_kept
    assert steps.generated == [[f"search-0-{i}" for i in range(4)]]


def test_low_after_retry_still_generates_and_never_hands_off_on_score() -> None:
    """The score is only a retry trigger: low twice still generates and answers."""
    steps = FakeSteps([0.0, 0.0], FOUND)
    tickets = MemoryTicketStore()
    outcome = _flow(steps, tickets).run("q", CUSTOMER)
    assert len(steps.searches) == 2 and len(steps.generated) == 1
    assert outcome.path.grade == "low" and outcome.path.handoff_reason is None
    assert outcome.text == FOUND.answer and tickets.tickets == []


def test_low_score_retry_spends_the_budget_so_not_found_hands_off() -> None:
    """One budget across both triggers: after a low-score retry, a not-found answer
    hands off without a second retry."""
    steps = FakeSteps([0.0, 0.5], NOT_FOUND_BARE)
    outcome = _flow(steps).run("q", CUSTOMER)
    assert len(steps.searches) == 2 and len(steps.generated) == 1
    assert outcome.path.retry_trigger == "low_score"
    assert outcome.path.handoff_reason == "not_found"


# ------------------------------------------------------- the not-found trigger


def test_not_found_retries_once_and_answers() -> None:
    """Passing grade, not found: one wider search, regenerate on 8 chunks, answer."""
    steps = FakeSteps([0.9, 0.9], [NOT_FOUND_BARE, FOUND])
    tickets = MemoryTicketStore()
    outcome = _flow(steps, tickets).run("How long is the warranty?", CUSTOMER)
    p = outcome.path
    assert len(steps.searches) == 2 and len(steps.generated) == 2
    assert (p.retry_trigger, p.retry_kept, p.retry_changed_outcome) == ("not_found", True, True)
    assert p.handoff_reason is None and tickets.tickets == []
    assert steps.generated[1] == [f"search-1-{i}" for i in range(8)]
    assert outcome.text == FOUND.answer and outcome.context_k == 8
    assert "regenerate" in outcome.timings and outcome.timings["generate"] == 3.0


def test_not_found_twice_hands_off_after_one_retry() -> None:
    """Still not found after the retry: hand off; the flow never retries twice."""
    steps = FakeSteps([0.9, 0.9], [NOT_FOUND_BARE, NOT_FOUND_CITED])
    tickets = MemoryTicketStore()
    outcome = _flow(steps, tickets).run("Is there a student discount?", CUSTOMER)
    p = outcome.path
    assert len(steps.searches) == 2 and len(steps.generated) == 2
    assert (p.retry_trigger, p.retry_changed_outcome, p.handoff_reason) == (
        "not_found",
        False,
        "not_found",
    )
    assert outcome.text == f"{NOT_FOUND_CITED.answer}\n\n{PROMPTS['handoff_customer']}"
    [ticket] = tickets.tickets
    assert ticket.closest_doc_ids == [
        "search-1-0",
        "search-1-1",
        "search-1-2",
        "search-1-3",
        "search-1-4",
        "search-1-5",
        "search-1-6",
        "search-1-7",
    ]


def test_invalid_output_also_triggers_the_not_found_retry() -> None:
    """Invalid output counts as a failed check: one retry, then answer."""
    steps = FakeSteps([0.9, 0.9], [INVALID, FOUND])
    outcome = _flow(steps).run("q", CUSTOMER)
    assert outcome.path.retry_trigger == "not_found" and outcome.path.retry_changed_outcome


def test_retry_searches_the_original_message_plus_resolved_products() -> None:
    """The wider search: the ORIGINAL message plus catalogue name and code of each
    resolved product, fetch_k 30, 8 chunks; reranked against the standalone question,
    with the same user, regions and codes as the first search."""
    steps = FakeSteps([0.9, 0.9], [NOT_FOUND_BARE, FOUND], codes=["VM-55Q"])
    _flow(steps).run("Can I send it back?", CUSTOMER, retrieve_k=5)
    first, retry = steps.searches
    assert first["question"] == "rewriter keywords" and first["fetch_k"] is None
    assert retry["question"] == 'Can I send it back? VisionMax 55" 4K TV VM-55Q'
    assert (first["k"], retry["k"], retry["fetch_k"]) == (5, 8, 30)
    for key in ("user", "regions", "extra_codes", "rerank_query"):
        assert first[key] == retry[key], key
    assert retry["rerank_query"] == "Can I send it back? (standalone)"
    assert first["user"] is CUSTOMER and first["regions"] == ("UK", "ALL")


def test_retry_query_adds_only_catalogue_products() -> None:
    """Names and codes come from the catalogue; with no product, the message alone."""
    assert retry_query("Can I send it back?", []) == "Can I send it back?"
    assert retry_query("Is it waterproof?", [TV]) == 'Is it waterproof? VisionMax 55" 4K TV VM-55Q'


# ------------------------------------------------------------------- handoff


def test_customer_handoff_writes_a_ticket_and_shows_the_cited_not_found_answer() -> None:
    """Valid, not found, with citations: the model's answer above the polite message."""
    tickets = MemoryTicketStore()
    outcome = _flow(FakeSteps([0.9, 0.9], NOT_FOUND_CITED), tickets).run(
        "Is there a student discount?", CUSTOMER
    )
    assert outcome.path.handoff_reason == "not_found"
    assert outcome.text == f"{NOT_FOUND_CITED.answer}\n\n{PROMPTS['handoff_customer']}"
    assert outcome.model_text == NOT_FOUND_CITED.answer
    [ticket] = tickets.tickets
    assert outcome.path.ticket_id == ticket.ticket_id
    assert (ticket.user_id, ticket.region, ticket.reason) == ("cust_uk_01", "UK", "not_found")
    assert ticket.question == "Is there a student discount?"


@pytest.mark.parametrize("result", [NOT_FOUND_BARE, INVALID])
def test_fixed_message_alone_without_valid_cited_text(result: GroundedResult) -> None:
    """Not found without citations, or invalid output: the fixed message alone."""
    tickets = MemoryTicketStore()
    outcome = _flow(FakeSteps([0.9, 0.9], result), tickets).run("q", CUSTOMER)
    assert outcome.text == PROMPTS["handoff_customer"]
    assert outcome.model_text is None
    assert len(tickets.tickets) == 1
    expected = "invalid_output" if result is INVALID else "not_found"
    assert outcome.path.handoff_reason == expected


def test_agent_handoff_lists_the_closest_sources_and_writes_no_ticket() -> None:
    """Agents get 'not found in the knowledge base' and the closest sources; no ticket."""
    tickets = MemoryTicketStore()
    outcome = _flow(FakeSteps([0.9, 0.9], NOT_FOUND_BARE), tickets).run("q", AGENT)
    closest = "\n".join(f"- Doc 1 (search-1-{i})" for i in range(8))
    assert outcome.text == f"Not found in the knowledge base. Closest sources:\n{closest}"
    assert outcome.path.handoff_reason == "not_found" and outcome.path.ticket_id is None
    assert tickets.tickets == []


def test_check_reasons_and_when_model_text_is_shown() -> None:
    """The check's three reasons, and the display rule from the approval."""
    assert handoff_reason(FOUND) is None
    assert handoff_reason(NOT_FOUND_CITED) == "not_found"
    assert handoff_reason(INVALID) == "invalid_output"
    assert handoff_reason(GroundedResult("x", (), True, True, 1)) == "no_citations"
    assert [shows_model_text(r) for r in (FOUND, NOT_FOUND_CITED, NOT_FOUND_BARE, INVALID)] == [
        False,
        True,
        False,
        False,
    ]
    text, model = handoff_text(AGENT, NOT_FOUND_CITED, [], PROMPTS)
    assert text.endswith("Closest sources:\n- none") and model == NOT_FOUND_CITED.answer


def test_jsonl_ticket_store_appends(tmp_path: Path) -> None:
    """Tickets append one JSON line each, creating the folder."""
    store = JsonlTicketStore(tmp_path / "tickets" / "tickets.jsonl")
    tickets = MemoryTicketStore()
    _flow(FakeSteps([0.9, 0.9], NOT_FOUND_BARE), tickets).run("q", CUSTOMER)
    store.add(tickets.tickets[0])
    store.add(tickets.tickets[0])
    lines = store.path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2 and json.loads(lines[0])["user_id"] == "cust_uk_01"


# ------------------------------------- the retry through the real pipeline and roles


def _describe_filter(filters: object) -> str:
    """A comparable description of a region filter (an OR of region equalities)."""
    if filters is None:
        return "none"
    return repr([(f.target, f.operator.value, f.value) for f in filters.filters])  # type: ignore[attr-defined]


class Recorder:
    """A per-collection retriever that records each search and returns two chunks."""

    def __init__(self, collection: str, log: list[tuple]) -> None:
        """Log searches as (collection, k, filters)."""
        self.collection = collection
        self.log = log

    def search(self, query: str, k: int, **options: object) -> list[RetrievedChunk]:
        """Two chunks from this collection."""
        self.log.append((self.collection, k, _describe_filter(options.get("filters"))))
        audience = "internal" if self.collection.endswith("internal") else "public"
        return [
            RetrievedChunk("text", f"{self.collection}-{n}", "T", n, audience, "ALL", 0.5)
            for n in range(2)
        ]


VALID_REPLY = json.dumps({"answer": "a [1]", "citations": [1], "found_in_sources": True})
NOT_FOUND_REPLY = json.dumps(
    {"answer": "I can't find that.", "citations": [], "found_in_sources": False}
)
LOW, HIGH = -12.0, 5.0  # reranker logits: sigmoid ~6e-6 (grade low) and ~0.99 (pass)


def _v6_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    tickets: MemoryTicketStore,
    reply: str = VALID_REPLY,
    logit: float = LOW,
) -> tuple[Pipeline, list[str], list[tuple], list[object]]:
    """v6 (without the rewriter) on fake role connections and a scripted reranker."""
    for var in ("WEAVIATE_ADMIN_KEY", "WEAVIATE_CUSTOMER_KEY", "WEAVIATE_AGENT_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "test")
    monkeypatch.setenv("WEAVIATE_CUSTOMER_KEY", "customer-secret")
    monkeypatch.setenv("WEAVIATE_AGENT_KEY", "agent-secret")
    settings = load_settings(env_file=None)
    opened: list[str] = []
    clients: list[object] = []
    log: list[tuple] = []

    def opener(key: SecretStr) -> SimpleNamespace:
        opened.append(key.get_secret_value())
        client = SimpleNamespace(
            collections=SimpleNamespace(
                exists=lambda name: True, get=lambda name: SimpleNamespace(name=name)
            ),
            close=lambda: None,
        )
        clients.append(client)
        return client

    monkeypatch.setattr(
        "rag_assistant.retrieval.access.make_retriever",
        lambda stage, collection, embedder, manifest: Recorder(collection.name, log),
    )
    stage = V6.model_copy(update={"query_rewrite": False, "use_history": False})
    roles = RoleRetrievers(stage, settings, object(), Path("unused"), opener=opener)  # type: ignore[arg-type]
    reranker = Reranker("fake", scorer=lambda q, docs: [logit] * len(docs))
    generator = GroundedGenerator(lambda p: reply, PROMPTS)
    pipeline = Pipeline(stage, roles, generator, reranker=reranker, tickets=tickets)
    return pipeline, opened, log, clients


@pytest.mark.parametrize(
    ("reply", "logit", "trigger"),
    [(VALID_REPLY, LOW, "low_score"), (NOT_FOUND_REPLY, HIGH, "not_found")],
)
def test_customer_retry_reuses_the_connection_collection_and_filter(
    monkeypatch: pytest.MonkeyPatch, reply: str, logit: float, trigger: str
) -> None:
    """Either trigger retries through the same customer connection (opened once, customer
    key), the public collection only, with an identical region filter. Never internal."""
    pipeline, opened, log, clients = _v6_pipeline(monkeypatch, MemoryTicketStore(), reply, logit)
    answer = pipeline.answer("Is there a student discount?", CUSTOMER)
    assert answer.path is not None and answer.path.retry_trigger == trigger
    assert opened == ["customer-secret"] and len(clients) == 1
    assert [(c, k) for c, k, _ in log] == [("Chunks_v5_public", 20), ("Chunks_v5_public", 30)]
    assert log[0][2] == log[1][2] and "UK" in log[0][2]
    assert all(c.audience == "public" for c in answer.retrieved)


@pytest.mark.parametrize(("reply", "logit"), [(VALID_REPLY, LOW), (NOT_FOUND_REPLY, HIGH)])
def test_agent_retry_uses_the_agent_connection_for_both_searches(
    monkeypatch: pytest.MonkeyPatch, reply: str, logit: float
) -> None:
    """Agents retry on their own single connection, over the same two collections."""
    pipeline, opened, log, _ = _v6_pipeline(monkeypatch, MemoryTicketStore(), reply, logit)
    pipeline.answer("What is the goodwill limit?", AGENT)
    assert opened == ["agent-secret"]
    assert [(c, k) for c, k, _ in log] == [
        ("Chunks_v5_public", 20),
        ("Chunks_v5_internal", 20),
        ("Chunks_v5_public", 30),
        ("Chunks_v5_internal", 30),
    ]
    assert len({f for _, _, f in log}) == 1  # no filter for agents, the same each time


def test_corrective_stage_needs_a_ticket_store() -> None:
    """v6 can't be built without somewhere to put customer tickets."""
    roles = RoleRetrievers.__new__(RoleRetrievers)
    generator = GroundedGenerator(lambda p: "", PROMPTS)
    with pytest.raises(ValueError, match="ticket store"):
        Pipeline(V6, roles, generator, reranker=object(), rewriter=object())  # type: ignore[arg-type]


def test_invalid_structured_output_hands_off_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    """Invalid output (twice per generation) triggers the one retry, then hands off."""
    tickets = MemoryTicketStore()
    pipeline, _, log, _ = _v6_pipeline(monkeypatch, tickets, reply="not JSON", logit=HIGH)
    answer = pipeline.answer("Is there a student discount?", CUSTOMER)
    assert answer.grounded is not None and not answer.grounded.valid
    assert answer.grounded.attempts == 2
    assert answer.path is not None and answer.path.retry_trigger == "not_found"
    assert answer.path.handoff_reason == "invalid_output" and len(log) == 2
    assert answer.text == PROMPTS["handoff_customer"] and answer.model_text is None
    assert answer.sources == [] and len(tickets.tickets) == 1


# ------------------------------------------- layer 3 in the flow (Session 9, v7)


def _guard() -> OutputGuard:
    """The configured output guard."""
    i = load_injection_settings()
    return OutputGuard(i.claim_phrases, i.attribution_terms, i.voltcart_policy_terms)


LIFETIME = GroundedResult("It has a lifetime warranty [1].", (1,), True, True, 1)


def _guarded(steps: FakeSteps, tickets: MemoryTicketStore | None = None) -> CorrectiveFlow:
    """v7's flow: v6's settings plus the guard and the 3-generation cap."""
    return CorrectiveFlow(
        steps, tickets or MemoryTicketStore(), PROMPTS, 0.1, 30, 8, 4, _guard(), 3
    )


def test_guard_passes_a_supported_answer_without_regenerating() -> None:
    """No number or promise to check: answered, one generation, guard silent."""
    steps = FakeSteps([0.9], FOUND)
    outcome = _guarded(steps).run("How do I reset the XR-2200?", CUSTOMER)
    p = outcome.path
    assert (p.guard_triggered, p.guard_regenerated, p.generations) == (False, False, 1)
    assert outcome.text == FOUND.answer and steps.notes == [None]


def test_guard_failure_regenerates_once_with_a_note_then_answers() -> None:
    """A rejected answer is regenerated once, with the problems as a note."""
    steps = FakeSteps([0.9], [LIFETIME, FOUND])
    outcome = _guarded(steps).run("What warranty does it have?", CUSTOMER)
    p = outcome.path
    assert (p.guard_triggered, p.guard_regenerated, p.generations) == (True, True, 2)
    assert p.handoff_reason is None and outcome.text == FOUND.answer
    assert steps.notes[0] is None and "lifetime warranty" in str(steps.notes[1])
    assert "regenerate" in outcome.timings


def test_guard_failing_twice_hands_off_with_the_fixed_message() -> None:
    """Rejected again after the one regeneration: hand off, never show the bad text."""
    tickets = MemoryTicketStore()
    steps = FakeSteps([0.9], LIFETIME)
    outcome = _guarded(steps, tickets).run("What warranty does it have?", CUSTOMER)
    assert outcome.path.handoff_reason == "output_guard" and outcome.path.generations == 2
    assert outcome.text == PROMPTS["handoff_customer"] and outcome.model_text is None
    assert len(tickets.tickets) == 1 and tickets.tickets[0].reason == "output_guard"


def test_a_rejected_not_found_text_is_hidden_from_the_handoff() -> None:
    """The model's not-found text shown above a handoff must pass the guard too."""
    bad = GroundedResult(
        "I can't find it, but there's a lifetime warranty [1].", (1,), False, True, 1
    )
    outcome = _guarded(FakeSteps([0.9, 0.9], bad)).run("q", CUSTOMER)
    assert outcome.path.handoff_reason == "not_found"
    assert outcome.text == PROMPTS["handoff_customer"] and outcome.model_text is None


def test_worst_case_path_stops_at_three_generations() -> None:
    """The longest path: rejected -> regenerated but not found -> retried -> found but
    rejected again. Three generations, then a handoff; never a fourth."""
    steps = FakeSteps([0.9, 0.9], [LIFETIME, NOT_FOUND_BARE, LIFETIME, FOUND])
    outcome = _guarded(steps).run("q", CUSTOMER)
    p = outcome.path
    assert len(steps.generated) == 3 and p.generations == 3
    assert (p.guard_regenerated, p.retry_trigger) == (True, "not_found")
    assert p.handoff_reason == "output_guard"


def test_the_cap_blocks_a_retry_when_generations_are_spent() -> None:
    """With a cap of 2: rejected, regenerated (2), not found: no third generation."""
    steps = FakeSteps([0.9, 0.9], [LIFETIME, NOT_FOUND_BARE, FOUND])
    flow = CorrectiveFlow(steps, MemoryTicketStore(), PROMPTS, 0.1, 30, 8, 4, _guard(), 2)
    outcome = flow.run("q", CUSTOMER)
    assert len(steps.generated) == 2 and not outcome.path.retry_used
    assert outcome.path.handoff_reason == "not_found"
    with pytest.raises(ValueError, match="max_generations"):
        CorrectiveFlow(steps, MemoryTicketStore(), PROMPTS, 0.1, 30, 8, 4, _guard(), 0)

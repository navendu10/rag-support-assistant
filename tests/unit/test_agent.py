"""Unit tests for the v8 agent (Session 10): business rules, the order store, the tools,
isolation, the step limit, the generation cap and the guard in agent mode. No network."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import date
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from generators.facts import ORDERS
from rag_assistant.agent.graph import AgentRunner, claimed_ticket_problems
from rag_assistant.agent.orders import OrderStore
from rag_assistant.agent.rules import (
    add_months,
    load_business_rules,
    return_window,
    warranty_periods,
)
from rag_assistant.agent.tools import (
    TOOL_SPECS,
    AgentSession,
    KnowledgeResult,
    ToolCall,
    check_return_eligibility,
    check_warranty,
    draft_ticket,
    get_order,
    list_my_orders,
    make_tools,
)
from rag_assistant.config import load_injection_settings, load_prompts, load_stage
from rag_assistant.eval.agent_eval import ToolCallRecord, isolation_problems, tool_problems
from rag_assistant.eval.metrics import (
    AgentRecord,
    ItemChecks,
    Judgement,
    ResultRow,
    judge_sources,
)
from rag_assistant.retrieval.vector import RetrievedChunk
from rag_assistant.schemas import AgentScenario, User, load_agent_scenarios, load_catalogue
from rag_assistant.security.output_guard import OutputGuard

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RULES = load_business_rules()
TODAY = RULES.reference_date
PROMPTS = load_prompts()
INJECTION = load_injection_settings()
GUARD = OutputGuard(
    INJECTION.claim_phrases, INJECTION.attribution_terms, INJECTION.voltcart_policy_terms
)
UK = User(user_id="cust_uk_01", name="Oliver Bennett", role="customer", region="UK")
US = User(user_id="cust_us_01", name="Maya Johnson", role="customer", region="US")
STAFF = User(user_id="agent_01", name="Support Agent 01", role="agent", region="ALL")
ORDER_TOOLS = (get_order, check_warranty, check_return_eligibility)


# ------------------------------------------------------------------ rules


@pytest.mark.parametrize(
    ("start", "months", "end"),
    [
        (date(2026, 3, 12), 24, date(2028, 3, 12)),
        (date(2024, 2, 29), 12, date(2025, 2, 28)),  # leap day, non-leap year: clamp
        (date(2024, 2, 29), 48, date(2028, 2, 29)),  # leap day to leap day
        (date(2026, 1, 31), 1, date(2026, 2, 28)),
        (date(2026, 10, 31), 6, date(2027, 4, 30)),
    ],
)
def test_add_months_clamps_to_the_last_day(start: date, months: int, end: date) -> None:
    """Warranty end dates: same day N months later, or that month's last day."""
    assert add_months(start, months) == end


def test_return_window_includes_its_last_day_and_not_the_next() -> None:
    """15 days from 20 October: 4 November is the last day; 5 November is too late."""
    delivered = date(2026, 10, 20)
    last = return_window("UK", "Audio", True, delivered, RULES, date(2026, 11, 4))
    after = return_window("UK", "Audio", True, delivered, RULES, date(2026, 11, 5))
    assert (last.last_day, last.eligible, after.eligible) == (date(2026, 11, 4), True, False)
    assert last.days_since_delivery == 15


@pytest.mark.parametrize(
    ("region", "category", "opened", "days", "fee"),
    [
        ("UK", "Audio", True, 15, 0),
        ("UK", "Laptops", True, 30, 0),
        ("UK", "Laptops", False, 30, 0),
        ("IN", "TVs", True, 7, 0),
        ("US", "Laptops", True, 15, 10),
        ("US", "Audio", True, 15, 0),
        ("US", "Audio", False, 30, 0),
    ],
)
def test_return_windows_and_fees(
    region: str, category: str, opened: bool, days: int, fee: int
) -> None:
    """Windows by region, condition and category; the US fee is for opened non-audio."""
    window = return_window(region, category, opened, date(2026, 10, 1), RULES, TODAY)
    assert (window.window_days, window.fee_percent) == (days, fee)


def test_warranty_periods() -> None:
    """Standard 12 months; the VisionMax panel 24; marketplace items none of VoltCart's."""
    tv = warranty_periods("VM-55Q", date(2026, 3, 12), RULES, TODAY)
    assert [(p.covers, p.months, p.ends) for p in tv] == [
        ("panel defects", 24, date(2028, 3, 12)),
        ("other parts", 12, date(2027, 3, 12)),
    ]
    old = warranty_periods("NB-14", date(2025, 10, 31), RULES, TODAY)
    assert old[0].ends == date(2026, 10, 31) and not old[0].covered
    assert warranty_periods("QC-USBC", date(2026, 10, 1), RULES, TODAY) == []


def _table_rows(text: str) -> list[list[str]]:
    """Every Markdown table row as stripped cells."""
    return [
        [cell.strip() for cell in line.strip().strip("|").split("|")]
        for line in text.splitlines()
        if line.strip().startswith("|") and "---" not in line
    ]


@pytest.mark.parametrize("region", ["UK", "IN", "US"])
def test_return_rules_match_the_policy_text(generated_dir: Path, region: str) -> None:
    """Every row of each 2026 returns table agrees with business_rules.yaml."""
    text = (generated_dir / "policies" / f"returns-{region.lower()}-2026.md").read_text()
    terms = RULES.returns.regions[region]
    rows = [r for r in _table_rows(text) if r[2] in {"Sealed", "Opened"}]
    assert len(rows) == 16
    for category, _examples, condition, window, conditions in rows:
        days = int(window.split()[0])
        if condition == "Sealed":
            assert days == terms.sealed_days, (region, category)
        else:
            expected_days, fee = terms.opened_window(category)
            assert days == expected_days, (region, category)
            assert (f"{fee}% restocking fee" in conditions) == bool(fee), (region, category)
    packaging = "every return must be in its original packaging" in text
    assert packaging == terms.original_packaging_required, region
    refund = re.search(r"within (\d+) working days", text)
    assert refund and int(refund.group(1)) == RULES.returns.refund_working_days


def test_warranty_rules_match_the_policy_text(generated_dir: Path) -> None:
    """The coverage table says exactly what business_rules.yaml says."""
    text = (generated_dir / "policies" / "policy-warranty.md").read_text(encoding="utf-8")
    rows = {(r[0], r[1]): r[2] for r in _table_rows(text) if len(r) == 3}
    assert rows[("All VoltCart products unless listed below", "All parts")] == (
        f"{RULES.warranty.standard_months} months"
    )
    tv = RULES.warranty.products["VM-55Q"]
    panel = next(v for (p, part), v in rows.items() if "VM-55Q" in p and "Panel" in part)
    other = next(v for (p, part), v in rows.items() if "VM-55Q" in p and "Other" in part)
    assert (panel, other) == (
        f"{tv.panel_defects_months} months",
        f"{tv.other_parts_months} months",
    )
    assert "Coverage starts on the date your order is delivered" in text


def test_marketplace_codes_are_exactly_the_listings(generated_dir: Path) -> None:
    """Every listing's product, and nothing else, follows the seller's warranty."""
    manifest = json.loads((generated_dir / "manifest.json").read_text(encoding="utf-8"))
    listed = {d["product_code"] for d in manifest["documents"] if d["doc_type"] == "listing"}
    assert set(RULES.warranty.marketplace_codes) == listed


# ----------------------------------------------------------- order store


@pytest.fixture(scope="module")
def store(generated_dir: Path) -> OrderStore:
    """The generated order database, read-only."""
    return OrderStore(generated_dir / "orders.db")


def _session(store: OrderStore, user: User, generated_dir: Path) -> AgentSession:
    """A session with a knowledge stub that answers nothing."""
    catalogue = {p.code: p for p in load_catalogue(generated_dir / "manifest.json")}
    return AgentSession(user, store, RULES, catalogue, lambda q: KnowledgeResult(False, "", []))


def test_the_database_is_read_only(store: OrderStore) -> None:
    """The agent's connection can't write."""
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        store._db.execute("DELETE FROM orders")  # noqa: SLF001


def test_customers_see_only_their_own_orders(store: OrderStore, generated_dir: Path) -> None:
    """list_my_orders returns exactly the user's orders from facts.ORDERS."""
    session = _session(store, UK, generated_dir)
    mine = [o["order_id"] for o in list_my_orders(session)["orders"]]
    assert mine == sorted(o.order_id for o in ORDERS if o.customer_id == "cust_uk_01")


@pytest.mark.parametrize("tool", ORDER_TOOLS)
def test_another_customers_order_looks_exactly_like_a_missing_one(
    store: OrderStore, generated_dir: Path, tool: object
) -> None:
    """Order tools return the identical not-found result, so ids can't be probed."""
    session = _session(store, UK, generated_dir)
    other = tool(session, "VC-20001")  # type: ignore[operator]
    missing = tool(session, "VC-99999")  # type: ignore[operator]
    assert other["status"] == missing["status"] == "not_found"
    assert {**other, "order_id": "X"} == {**missing, "order_id": "X"}
    ticket = draft_ticket(session, "VC-20001", "return", "x")
    assert {**ticket, "order_id": "X"} == {
        **draft_ticket(session, "VC-99999", "return", "x"),
        "order_id": "X",
    }


def test_staff_can_look_up_any_order_and_have_none_of_their_own(
    store: OrderStore, generated_dir: Path
) -> None:
    """agent_01: get_order works for any id; list_my_orders is empty."""
    session = _session(store, STAFF, generated_dir)
    assert get_order(session, "VC-20001")["order"]["customer_id"] == "cust_uk_02"
    listed = list_my_orders(session)
    assert listed["orders"] == [] and "no orders" in listed["note"]


def test_staff_draft_ticket_takes_the_customer_from_the_order(
    store: OrderStore, generated_dir: Path
) -> None:
    """The ticket's customer is the order's, never anything the model said."""
    ticket = draft_ticket(_session(store, STAFF, generated_dir), "VC-20002", "repair", "pixels")
    assert ticket["ticket"]["customer_id"] == "cust_uk_03"
    assert ticket["ticket"]["drafted_by"] == "agent_01"


def test_no_tool_accepts_a_user_or_customer_id() -> None:
    """The user comes from the session only: no input model has such a field."""
    for name, (model, _logic, _description) in TOOL_SPECS.items():
        fields = set(model.model_fields)
        assert not {f for f in fields if re.search(r"user|customer|role|account", f)}, name
        assert model.model_config.get("extra") == "forbid", name


def test_tool_results_compute_the_planted_cases(store: OrderStore, generated_dir: Path) -> None:
    """The TV, the two earbuds, the laptop fee and the late delivery, as designed."""
    uk, us = _session(store, UK, generated_dir), _session(store, US, generated_dir)
    tv = check_warranty(uk, "VC-10001")
    assert tv["periods"][0] == {
        "covers": "panel defects",
        "months": 24,
        "ends": "2028-03-12",
        "covered": True,
    }
    inside, outside = (check_return_eligibility(uk, i) for i in ("VC-10002", "VC-10003"))
    assert (inside["eligible"], inside["last_day"], outside["eligible"]) == (
        True,
        "2026-11-04",
        False,
    )
    laptop = check_return_eligibility(us, "VC-10004")
    assert (laptop["eligible"], laptop["restocking_fee_percent"]) == (True, 10)
    india = User(user_id="cust_in_01", name="Priya Sharma", role="customer", region="IN")
    late = get_order(_session(store, india, generated_dir), "VC-10006")
    assert late["late_by_days"] == 5


def _fingerprint(paths: list[Path]) -> dict[str, str]:
    """Hash every file under ``paths``."""
    return {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest()
        for root in paths
        for p in ([root] if root.is_file() else sorted(root.rglob("*")))
        if p.is_file()
    }


def test_draft_ticket_writes_nothing(store: OrderStore, generated_dir: Path) -> None:
    """Drafting changes no file: not the database, not the generated data, not tickets."""
    tickets = PROJECT_ROOT / "data" / "tickets"
    before = (
        _fingerprint([generated_dir, tickets])
        if tickets.exists()
        else _fingerprint([generated_dir])
    )
    tools = {t.name: t for t in make_tools(_session(store, UK, generated_dir))}
    draft = json.loads(
        tools["draft_ticket"].invoke({"order_id": "VC-10001", "kind": "repair", "reason": "lines"})
    )
    after = (
        _fingerprint([generated_dir, tickets])
        if tickets.exists()
        else _fingerprint([generated_dir])
    )
    assert (draft["status"], draft["written"]) == ("draft", False)
    assert before == after


# -------------------------------------------------------------- the graph


class ScriptedModel:
    """A fake chat model: replays scripted AIMessages; ``bind_tools`` returns itself."""

    def __init__(self, replies: list[AIMessage], repeat_last: bool = False) -> None:
        """``repeat_last``: keep answering with the last reply forever (a looping model)."""
        self.replies = replies
        self.repeat_last = repeat_last
        self.calls: list[list] = []

    def bind_tools(self, tools: list) -> ScriptedModel:
        """The same model; the tools are ignored."""
        return self

    def invoke(self, messages: list) -> AIMessage:
        """The next scripted reply."""
        self.calls.append(list(messages))
        if self.repeat_last and len(self.calls) > len(self.replies):
            return self.replies[-1]
        return self.replies[len(self.calls) - 1]


def _call(name: str, args: dict, n: int) -> AIMessage:
    """An AIMessage asking for one tool call."""
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"c{n}"}])


def _runner(model: ScriptedModel, max_tool_calls: int = 6) -> AgentRunner:
    """The real graph over a fake model, with v8's limits."""
    return AgentRunner(model, PROMPTS, GUARD, max_tool_calls, 3)  # type: ignore[arg-type]


def test_a_looping_model_stops_at_the_step_limit(store: OrderStore, generated_dir: Path) -> None:
    """A model that only ever calls tools is cut off at 6 calls, then answers once
    without tools; that final call counts as one of the 3 generations."""
    loop = ScriptedModel(
        [*(_call("list_my_orders", {}, n) for n in range(10)), AIMessage("You have 3 orders.")]
    )
    loop.replies = [_call("list_my_orders", {}, n) for n in range(6)] + [
        AIMessage("You have 3 orders.")
    ]
    result = _runner(loop).run(_session(store, UK, generated_dir), "list everything, forever")
    assert result.tool_calls == 6 and len(result.calls) == 6
    assert result.hit_step_limit and result.generations == 1
    assert result.text == "You have 3 orders." and result.model_calls == 7


def test_a_reply_asking_for_more_calls_than_allowed_is_cut(
    store: OrderStore, generated_dir: Path
) -> None:
    """Five calls in one reply with a limit of 3: only 3 run."""
    many = AIMessage(
        content="",
        tool_calls=[{"name": "list_my_orders", "args": {}, "id": f"m{n}"} for n in range(5)],
    )
    model = ScriptedModel([many, AIMessage("Done.")])
    result = _runner(model, max_tool_calls=3).run(_session(store, UK, generated_dir), "q")
    assert result.tool_calls == 3 and result.hit_step_limit


def test_guard_rejection_regenerates_once_then_falls_back(
    store: OrderStore, generated_dir: Path
) -> None:
    """An invented date fails the guard; the one regeneration invents again; the reply is
    the fixed fallback, and generations stay within the cap."""
    model = ScriptedModel(
        [
            _call("check_warranty", {"order_id": "VC-10001"}, 1),
            AIMessage("Your TV is covered until 1 January 2030."),
            AIMessage("Your TV is covered until 2 January 2030."),
        ]
    )
    result = _runner(model).run(_session(store, UK, generated_dir), "Is my TV covered?")
    assert result.outcome == "fallback" and result.text == PROMPTS["agent_fallback_customer"]
    assert result.generations == 2 and len(result.guard_problems) == 2


def test_tool_result_dates_and_figures_pass_the_guard(
    store: OrderStore, generated_dir: Path
) -> None:
    """Figures and dates from this turn's tool results are trusted."""
    model = ScriptedModel(
        [
            _call("check_warranty", {"order_id": "VC-10001"}, 1),
            AIMessage(
                "Your VisionMax (order VC-10001) was delivered on 12 March 2026; panel defects "
                "are covered for 24 months, until 12 March 2028."
            ),
        ]
    )
    result = _runner(model).run(_session(store, UK, generated_dir), "Is my TV covered?")
    assert result.outcome == "answered" and not result.guard_problems


def test_three_generations_is_a_hard_cap(store: OrderStore, generated_dir: Path) -> None:
    """With the step limit hit first, finalize is generation 1, the regeneration is 2, and
    nothing more runs: the model is called no more than 6 tool turns + 2 answers."""
    replies = [_call("list_my_orders", {}, n) for n in range(6)]
    replies += [
        AIMessage("Covered until 1 January 2030."),
        AIMessage("Covered until 2 January 2030."),
    ]
    model = ScriptedModel(replies)
    result = _runner(model).run(_session(store, UK, generated_dir), "q")
    assert result.generations <= 3 and result.outcome == "fallback"
    assert result.model_calls == 8 and len(model.calls) == 8


def test_staff_get_the_staff_system_prompt(store: OrderStore, generated_dir: Path) -> None:
    """The system prompt follows the role in the profile, with the reference date."""
    model = ScriptedModel([AIMessage("Hello.")])
    _runner(model).run(_session(store, STAFF, generated_dir), "hi")
    system = model.calls[0][0].content
    assert "SUPPORT AGENT" in system and TODAY.isoformat() in system


# ------------------------------------------------ tickets and the knowledge tool


def test_v8_writes_no_tickets_and_v7_still_does() -> None:
    """The switch keeps v6/v7 as evaluated and turns ticket writing off for v8."""
    assert load_stage("v7").handoff_writes_ticket and load_stage("v6").handoff_writes_ticket
    v8 = load_stage("v8")
    assert (v8.handoff_writes_ticket, v8.agent, v8.max_tool_calls) == (False, True, 6)


def test_the_agent_run_creates_nothing_under_data_tickets(
    store: OrderStore, generated_dir: Path
) -> None:
    """A whole agent turn with a not-found knowledge answer and a draft: no ticket file."""
    tickets = PROJECT_ROOT / "data" / "tickets"
    before = _fingerprint([tickets]) if tickets.exists() else {}
    model = ScriptedModel(
        [
            _call("search_knowledge_base", {"question": "student discount?"}, 1),
            _call("draft_ticket", {"order_id": "VC-10001", "kind": "repair", "reason": "x"}, 2),
            AIMessage("I couldn't find that; a draft ticket is ready for review."),
        ]
    )
    _runner(model).run(_session(store, UK, generated_dir), "q")
    after = _fingerprint([tickets]) if tickets.exists() else {}
    assert before == after


def test_knowledge_passages_are_numbered_across_the_turn(
    store: OrderStore, generated_dir: Path
) -> None:
    """Passages from several knowledge calls are K1, K2, ... and kept for the guard."""
    chunk = RetrievedChunk(
        "Opened audio: 15 days.", "returns-uk-2026", "Returns", 1, "public", "UK", 1.0
    )
    session = _session(store, UK, generated_dir)
    session.knowledge = lambda q: KnowledgeResult(True, "15 days [1].", [chunk])
    tools = {t.name: t for t in make_tools(session)}
    first = json.loads(tools["search_knowledge_base"].invoke({"question": "a"}))
    second = json.loads(tools["search_knowledge_base"].invoke({"question": "b"}))
    assert [p["id"] for p in first["passages"] + second["passages"]] == ["K1", "K2"]
    assert len(session.passages) == 2


# --------------------------------------------------------------- scenarios


def test_agent_scenarios_cover_the_brief() -> None:
    """About 12 Session 10 scenarios (s01-s14) over the required topics and both roles."""
    scenarios = [
        s
        for s in load_agent_scenarios(PROJECT_ROOT / "data" / "golden" / "agent_scenarios.jsonl")
        if s.id <= "s14"
    ]
    topics = {s.topic for s in scenarios}
    assert 12 <= len(scenarios) <= 16
    for topic in (
        "warranty_repair",
        "return_inside_window",
        "return_outside_window",
        "restocking_fee",
        "late_delivery",
        "knowledge_only",
        "other_customers_order",
        "agent_order_lookup",
        "agent_list_all",
        "direct_attack_all_orders",
    ):
        assert topic in topics, topic
    tv = next(s for s in scenarios if s.topic == "warranty_repair")
    assert tv.expected_tools == [
        "list_my_orders",
        "check_warranty",
        "search_knowledge_base",
        "draft_ticket",
    ]


def _scenario(**overrides: object) -> AgentScenario:
    """A scenario with defaults."""
    data = {
        "id": "s99",
        "topic": "t",
        "user_id": "cust_uk_01",
        "question": "q",
        "expected_tools": ["get_order"],
        "forbidden_tools": ["draft_ticket"],
        "reference_answer": "r",
        "must_not_contain": ["Amelia Clarke"],
    }
    return AgentScenario.model_validate({**data, **overrides})


def test_tool_correctness_scoring() -> None:
    """Missing expected tools and forbidden tools are both reported."""
    assert tool_problems(_scenario(), ["get_order"]) == ([], [])
    assert tool_problems(_scenario(), ["draft_ticket"]) == (["get_order"], ["draft_ticket"])


def test_isolation_scoring_checks_owners_and_phrases() -> None:
    """Another customer's order in a found result fails; a not-found echo doesn't."""
    owners = {"VC-10001": "cust_uk_01", "VC-20001": "cust_uk_02"}
    echo = ToolCallRecord(
        name="get_order",
        args={},
        status="not_found",
        result={"order_id": "VC-20001", "status": "not_found"},
    )
    leaked = ToolCallRecord(
        name="get_order", args={}, status="ok", result={"order": {"order_id": "VC-20001"}}
    )
    assert isolation_problems(_scenario(), UK, "Not found.", [echo], owners) == []
    problems = isolation_problems(_scenario(), UK, "Amelia Clarke's order", [leaked], owners)
    assert any("owned by cust_uk_02" in p for p in problems)
    assert any("Amelia Clarke" in p for p in problems)
    assert isolation_problems(_scenario(must_not_contain=[]), STAFF, "ok", [leaked], owners) == []


@pytest.mark.parametrize(
    ("answer", "claims"),
    [
        ("I'll create a draft repair ticket for you; an agent will review it.", True),
        ("**Draft repair ticket**\n- Kind: repair", True),
        ("I've raised a ticket for you.", True),
        ("A ticket has been opened.", True),
        ("I can draft a repair ticket if you like.", False),
        ("Would you like me to draft a ticket?", False),
        ("Your TV is covered until 2028-03-12.", False),
    ],
)
def test_a_claimed_ticket_needs_a_real_draft(answer: str, claims: bool) -> None:
    """Claiming a ticket without a draft_ticket draft is an invented action; offers pass."""
    drafted = [ToolCall("draft_ticket", {}, {"status": "draft"}, 1.0)]
    assert bool(claimed_ticket_problems(answer, [])) is claims
    assert claimed_ticket_problems(answer, drafted) == []


def test_an_invented_ticket_is_rejected_by_the_agent_guard(
    store: OrderStore, generated_dir: Path
) -> None:
    """The s01 failure: a ticket claimed with no draft_ticket call is regenerated."""
    model = ScriptedModel(
        [
            _call("check_warranty", {"order_id": "VC-10001"}, 1),
            AIMessage("It's covered. I'll create a draft repair ticket for you."),
            AIMessage("It's covered. Would you like me to draft a repair ticket?"),
        ]
    )
    result = _runner(model).run(_session(store, UK, generated_dir), "fix my TV")
    assert result.outcome == "answered" and result.generations == 2
    assert "draft_ticket drafted none" in result.guard_problems[0]
    assert "draft_ticket drafted none" in str(model.calls[-1][-1].content)


def test_the_invented_judge_sees_this_turns_tool_results() -> None:
    """For agent rows, the judge's sources include the order-tool facts."""
    record = AgentRecord(
        tools=["check_warranty"],
        tool_calls=1,
        model_calls=2,
        generations=1,
        hit_step_limit=False,
        outcome="answered",
        tool_facts=[
            "check_warranty(order_id=VC-10001): panel defects: 24 months, until 2028-03-12"
        ],
    )
    checks = ItemChecks(
        recall_hit=None,
        retrieval_leak=False,
        answer_leak=None,
        injection_fail=None,
        forbidden_hits=[],
    )
    base = {
        "id": "g009", "question": "q", "user_id": "cust_uk_01", "user_role": "customer",
        "user_region": "UK", "failure_tag": "ranking", "topic": "t", "history_turns": 0,
        "expected_doc_ids": [], "must_not_contain": [], "expect_handoff": False,
        "retrieved": [], "context_doc_ids": [], "context_audiences": [], "answer": "a",
        "checks": checks, "correctness": Judgement(verdict="CORRECT", reason="r"),
        "handoff": None, "latency_ms": {"total": 1.0},
    }  # fmt: skip
    agent_row = ResultRow.model_validate({**base, "agent": record})
    assert "TOOL RESULTS" in judge_sources("[1] Policy", agent_row)
    assert "2028-03-12" in judge_sources("[1] Policy", agent_row)
    assert judge_sources("[1] Policy", ResultRow.model_validate(base)) == "[1] Policy"

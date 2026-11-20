"""Unit tests for the MCP server and the v10 agent over MCP (Session 12). No network, no LLM.

In-process tests use the SDK's in-memory transport; the subprocess tests start the real
server (``python -m rag_assistant.mcp_server``) over stdio with a temporary data folder.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from mcp import ClientSession
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import LATEST_PROTOCOL_VERSION

from rag_assistant.agent.audit import MemoryAudit
from rag_assistant.agent.graph import AgentPipeline, AgentRunner
from rag_assistant.agent.knowledge import RetrievalContext
from rag_assistant.agent.orders import OrderStore
from rag_assistant.agent.rules import load_business_rules
from rag_assistant.agent.session import ChatSession
from rag_assistant.agent.tickets import Submitter, TicketDB
from rag_assistant.agent.tools import APPROVAL_TOOL_SPECS, AgentSession, KnowledgeResult
from rag_assistant.config import load_injection_settings, load_prompts, load_stage
from rag_assistant.mcp_server.client import MCPToolClient, mcp_tool_factory, model_schema
from rag_assistant.mcp_server.server import EXTERNAL_DRAFT_NOTE, Backend, build_server
from rag_assistant.retrieval.vector import RetrievedChunk
from rag_assistant.schemas import User, load_catalogue
from rag_assistant.security.output_guard import OutputGuard

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RULES = load_business_rules()
PROMPTS = load_prompts()
INJECTION = load_injection_settings()
GUARD = OutputGuard(
    INJECTION.claim_phrases, INJECTION.attribution_terms, INJECTION.voltcart_policy_terms
)
UK = User(user_id="cust_uk_01", name="Oliver Bennett", role="customer", region="UK")
US = User(user_id="cust_us_01", name="Maya Johnson", role="customer", region="US")
STAFF = User(user_id="agent_01", name="Support Agent 01", role="agent", region="ALL")
CHUNK = RetrievedChunk(
    "Opened audio products: 15 days from delivery.", "returns-uk-2026", "Returns (UK)", 1,
    "public", "UK", 0.91, section="Return windows", trust="store",
)  # fmt: skip
CALLS: list[tuple[str, dict]] = [
    ("list_my_orders", {}),
    ("get_order", {"order_id": "VC-10002"}),  # UK's own
    ("get_order", {"order_id": "VC-20001"}),  # another customer's
    ("get_order", {"order_id": "VC-99999"}),  # missing
    ("check_warranty", {"order_id": "VC-10001"}),
    ("check_warranty", {"order_id": "VC-20002"}),
    ("check_return_eligibility", {"order_id": "VC-10004"}),
    ("check_return_eligibility", {"order_id": "VC-10002"}),
    ("draft_ticket", {"kind": "return", "order_id": "VC-10002", "reason": "opened"}),
    ("draft_ticket", {"kind": "repair", "order_id": "VC-20002", "reason": "dead pixels"}),
    ("draft_ticket", {"kind": "handoff", "reason": "student discount?"}),
    ("search_knowledge_base", {"question": "opened earbuds return window"}),
]


@pytest.fixture(scope="module")
def orders(generated_dir: Path) -> OrderStore:
    """The generated order database, read-only."""
    return OrderStore(generated_dir / "orders.db")


@pytest.fixture(scope="module")
def catalogue(generated_dir: Path) -> dict:
    """The product catalogue."""
    return {p.code: p for p in load_catalogue(generated_dir / "manifest.json")}


def _stub_knowledge(user: User, sink: list[RetrievalContext]) -> Callable[[str], KnowledgeResult]:
    """A knowledge function with one store passage and a retrieval context (no LLM)."""

    def knowledge(question: str) -> KnowledgeResult:
        sink.append(RetrievalContext(context=[CHUNK], retrieved=[CHUNK], cited=[CHUNK]))
        return KnowledgeResult(True, "15 days [1].", [CHUNK])

    return knowledge


def _backend(orders: OrderStore, catalogue: dict, user: User, external: bool = False) -> Backend:
    """A server backend for ``user`` with the stub knowledge and an in-memory audit log."""
    return Backend(user, orders, RULES, catalogue, _stub_knowledge, MemoryAudit(), external)


def _direct(orders: OrderStore, catalogue: dict, user: User, name: str, args: dict) -> dict:
    """The in-process tool's result for a fresh session (what v9 runs)."""
    model, logic, _ = APPROVAL_TOOL_SPECS[name]
    session = AgentSession(user, orders, RULES, catalogue, _stub_knowledge(user, []), approval=True)
    return logic(session, **model.model_validate(args).model_dump())


def _without_draft_id(result: dict) -> dict:
    """A result with its random draft id blanked (the only field that differs per call)."""
    if isinstance(result.get("ticket"), dict):
        return {**result, "ticket": {**result["ticket"], "draft_id": "D-x"}}
    return result


async def _with_session(backend: Backend, work: Callable) -> object:
    """Run ``work(session)`` against an in-process server for ``backend``."""
    async with create_connected_server_and_client_session(build_server(backend)) as session:
        return await work(session)


# ------------------------------------------------------------------- parity


@pytest.mark.parametrize("user", [UK, US, STAFF], ids=lambda u: u.user_id)
def test_every_tool_returns_the_same_result_directly_and_over_mcp(
    orders: OrderStore, catalogue: dict, user: User
) -> None:
    """3 users x own, other customer's, missing orders, drafts and knowledge: identical."""
    backend = _backend(orders, catalogue, user)

    async def work(session: ClientSession) -> list:
        return [await session.call_tool(name, args) for name, args in CALLS]

    results = asyncio.run(_with_session(backend, work))
    for (name, args), remote in zip(CALLS, results, strict=True):
        direct = _direct(orders, catalogue, user, name, args)
        text = json.loads(remote.content[0].text)
        assert not remote.isError, (name, args)
        assert _without_draft_id(text) == _without_draft_id(direct), (user.user_id, name, args)
        assert remote.structuredContent["result"] == text
        if name != "draft_ticket":  # the JSON text itself, byte for byte
            assert remote.content[0].text == json.dumps(direct, ensure_ascii=False)


def test_the_knowledge_tool_returns_its_retrieval_context(
    orders: OrderStore, catalogue: dict
) -> None:
    """The structured result carries chunk ids, doc ids and trust, and it round-trips."""
    backend = _backend(orders, catalogue, UK)

    async def work(session: ClientSession) -> object:
        return await session.call_tool("search_knowledge_base", {"question": "returns"})

    result = asyncio.run(_with_session(backend, work))
    retrieval = result.structuredContent["retrieval"]
    expected = RetrievalContext(context=[CHUNK], retrieved=[CHUNK], cited=[CHUNK])
    assert retrieval == expected.to_dict()
    assert [(c["doc_id"], c["chunk_index"], c["trust"]) for c in retrieval["context"]] == [
        ("returns-uk-2026", 1, "store")
    ]
    assert RetrievalContext.from_dict(retrieval) == expected


def test_external_drafts_say_they_cant_be_submitted(orders: OrderStore, catalogue: dict) -> None:
    """Claude Code and other external clients get the note; the v10 agent doesn't."""

    async def work(session: ClientSession) -> object:
        return await session.call_tool("draft_ticket", {"kind": "handoff", "reason": "x"})

    external = asyncio.run(_with_session(_backend(orders, catalogue, UK, external=True), work))
    agent = asyncio.run(_with_session(_backend(orders, catalogue, UK, external=False), work))
    assert json.loads(external.content[0].text)["note"] == EXTERNAL_DRAFT_NOTE
    assert "note" not in json.loads(agent.content[0].text)


def test_every_call_is_audited_by_the_server(orders: OrderStore, catalogue: dict) -> None:
    """One tool_call event per call, via mcp, with the user; passages as doc ids only."""
    backend = _backend(orders, catalogue, UK)

    async def work(session: ClientSession) -> None:
        for name, args in CALLS:
            await session.call_tool(name, args)

    asyncio.run(_with_session(backend, work))
    events = backend.audit.events  # type: ignore[union-attr]
    assert [e["tool"] for e in events] == [name for name, _ in CALLS]
    assert {(e["event"], e["via"], e["user_id"]) for e in events} == {
        ("tool_call", "mcp", "cust_uk_01")
    }
    assert CHUNK.text not in json.dumps(events)


# -------------------------------------------------------------- the tool list


def test_the_tool_list_is_v9s_six_read_or_draft_tools(orders: OrderStore, catalogue: dict) -> None:
    """Names, descriptions and schemas are v9's; no identity field; no write tool."""

    async def work(session: ClientSession) -> list:
        return (await session.list_tools()).tools

    tools = asyncio.run(_with_session(_backend(orders, catalogue, UK), work))
    assert [t.name for t in tools] == list(APPROVAL_TOOL_SPECS)
    for tool in tools:
        model, _logic, description = APPROVAL_TOOL_SPECS[tool.name]
        assert tool.description == description
        assert tool.inputSchema == model.model_json_schema()
        fields = set(tool.inputSchema.get("properties", {}))
        assert not {
            f for f in fields if any(w in f for w in ("user", "customer", "role", "account"))
        }
        assert not any(w in tool.name for w in ("submit", "approve", "write", "admin", "delete"))


def test_the_model_is_given_v9s_exact_tool_definitions(orders: OrderStore, catalogue: dict) -> None:
    """What bind_tools sends for an MCP tool equals what it sends for the local one."""

    async def work(session: ClientSession) -> list:
        return (await session.list_tools()).tools

    for tool in asyncio.run(_with_session(_backend(orders, catalogue, UK), work)):
        model, _logic, description = APPROVAL_TOOL_SPECS[tool.name]
        local = StructuredTool.from_function(
            func=lambda **k: "", name=tool.name, description=description, args_schema=model
        )
        remote = StructuredTool.from_function(
            func=lambda **k: "", name=tool.name, description=tool.description,
            args_schema=model_schema(tool.inputSchema),
        )  # fmt: skip
        assert convert_to_openai_tool(remote) == convert_to_openai_tool(local), tool.name


@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("get_order", {"order_id": "VC-20001", "user_id": "cust_uk_02"}),
        ("list_my_orders", {"customer_id": "cust_us_01"}),
        ("draft_ticket", {"kind": "repair", "reason": "no order"}),
        ("draft_ticket", {"kind": "refund", "order_id": "VC-10002", "reason": "x"}),
    ],
)
def test_invalid_or_identity_arguments_are_refused(
    orders: OrderStore, catalogue: dict, name: str, args: dict
) -> None:
    """Unknown fields (a user id) and invalid values are errors; nothing runs."""
    backend = _backend(orders, catalogue, UK)

    async def work(session: ClientSession) -> object:
        return await session.call_tool(name, args)

    result = asyncio.run(_with_session(backend, work))
    assert result.isError
    assert backend.audit.events == []  # type: ignore[union-attr]


# ------------------------------------------------------- the v10 agent over MCP


class ScriptedModel:
    """A fake chat model: replays scripted AIMessages; ``bind_tools`` returns itself."""

    def __init__(self, replies: list[AIMessage]) -> None:
        """The replies, in order."""
        self.replies = list(replies)

    def bind_tools(self, tools: list) -> ScriptedModel:
        """The same model."""
        return self

    def invoke(self, messages: list) -> AIMessage:
        """The next scripted reply."""
        return self.replies.pop(0)


def _call(name: str, args: dict, n: int) -> AIMessage:
    """An AIMessage asking for one tool call."""
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"c{n}"}])


def _memory_opener(orders: OrderStore, catalogue: dict) -> Callable:
    """Open an in-memory MCP session with a server for the given user (agent mode)."""

    @asynccontextmanager
    async def opener(user: User) -> AsyncIterator[ClientSession]:
        server = build_server(_backend(orders, catalogue, user))
        async with create_connected_server_and_client_session(server) as session:
            yield session

    return opener


def _v10(orders: OrderStore, catalogue: dict, replies: list[AIMessage]) -> AgentPipeline:
    """v10's AgentPipeline: a scripted model, tools over an in-memory MCP server. It gets
    its own order store, because closing the pipeline closes it."""
    orders = OrderStore(orders.path)
    client = MCPToolClient(_memory_opener(orders, catalogue))
    submitter = Submitter(TicketDB(), orders, RULES, catalogue, 3)
    runner = AgentRunner(
        ScriptedModel(replies), PROMPTS, GUARD, 6, 3, submitter, None,  # type: ignore[arg-type]
        mcp_tool_factory(client),
    )  # fmt: skip
    return AgentPipeline(
        load_stage("v10"), None, runner, orders, RULES, catalogue, MemoryAudit(), client
    )


def test_the_v10_agent_drafts_pauses_and_submits_over_mcp(
    orders: OrderStore, catalogue: dict
) -> None:
    """The same approval flow as v9, with every tool call going through the MCP server."""
    present = (
        "Your AeroBuds Pro (VC-10002) can be returned until 2026-11-04. I've prepared a draft "
        "return ticket; nothing is sent until you approve it with the Approve control."
    )
    pipeline = _v10(
        orders,
        catalogue,
        [
            _call("check_return_eligibility", {"order_id": "VC-10002"}, 1),
            _call(
                "draft_ticket", {"kind": "return", "order_id": "VC-10002", "reason": "opened"}, 2
            ),
            AIMessage(present),
        ],
    )
    try:
        chat = ChatSession(pipeline, UK)
        answer = chat.send("Return my opened earbuds please")
        assert answer.agent.tools_used == ["check_return_eligibility", "draft_ticket"]
        draft = answer.agent.pending_draft
        assert draft and draft["thread_id"] == chat.thread_id and draft["order_id"] == "VC-10002"
        outcome = chat.approve(draft)
        assert outcome.status == "submitted"
        assert [t.order_id for t in pipeline.submitter.tickets.all()] == ["VC-10002"]
    finally:
        pipeline.close()


def test_passages_are_numbered_across_the_turn_and_context_is_kept(
    orders: OrderStore, catalogue: dict
) -> None:
    """Two knowledge calls in one turn: K1 then K2 (the server numbers each call from K1),
    and the Answer's context fields come from the structured result."""
    pipeline = _v10(
        orders,
        catalogue,
        [
            _call("search_knowledge_base", {"question": "opened audio returns"}, 1),
            _call("search_knowledge_base", {"question": "refund timing"}, 2),
            AIMessage("Opened audio products can be returned within 15 days [K1]."),
        ],
    )
    try:
        answer = pipeline.answer("Can I return opened earbuds?", UK)
        ids = [p["id"] for c in answer.agent.calls for p in c.result["passages"]]
        assert ids == ["K1", "K2"]
        assert answer.agent.guard_problems == []
        assert [c.doc_id for c in answer.context] == ["returns-uk-2026"]
        assert [c.doc_id for c in answer.retrieved] == ["returns-uk-2026"]
    finally:
        pipeline.close()


def test_a_draft_from_an_mcp_session_cant_be_approved_in_a_chat(
    orders: OrderStore, catalogue: dict
) -> None:
    """A draft id made by an external MCP client is refused by /approve in a chat session."""

    async def work(session: ClientSession) -> object:
        args = {"kind": "return", "order_id": "VC-10002", "reason": "opened"}
        return await session.call_tool("draft_ticket", args)

    made = asyncio.run(_with_session(_backend(orders, catalogue, UK, external=True), work))
    ticket = json.loads(made.content[0].text)["ticket"]
    pipeline = _v10(orders, catalogue, [])
    try:
        chat = ChatSession(pipeline, UK)
        outcome = chat.approve({"draft_id": ticket["draft_id"], "hash": "0" * 64})
        assert outcome.status == "refused"
        assert pipeline.submitter.tickets.all() == []
    finally:
        pipeline.close()


# ---------------------------------------------------------- the real process


def _server_env(generated_dir: Path) -> dict[str, str]:
    """Environment for a server process: dummy keys and the temporary generated data."""
    return {
        **os.environ,
        "GROQ_API_KEY": "gsk_dummy_key_for_tests_0000",
        "WEAVIATE_CUSTOMER_KEY": "dummy-customer-key-0000",
        "PATHS__GENERATED_DIR": str(generated_dir),
        "PYTHONIOENCODING": "utf-8",
    }


def _start(args: list[str], env: dict[str, str]) -> subprocess.Popen:
    """``python -m rag_assistant.mcp_server ...`` with pipes."""
    return subprocess.Popen(
        [sys.executable, "-m", "rag_assistant.mcp_server", *args],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        cwd=PROJECT_ROOT,
    )


@pytest.mark.parametrize("args", [["--user", "nobody"], []], ids=["unknown", "missing"])
def test_the_server_refuses_to_start_without_a_valid_user(
    generated_dir: Path, args: list[str]
) -> None:
    """Exit 2, a reason on stderr, nothing on stdout."""
    env = {k: v for k, v in _server_env(generated_dir).items() if k != "VOLTCART_MCP_USER"}
    process = _start(args, env)
    out, err = process.communicate(timeout=120)
    assert process.returncode == 2
    assert out == b""
    assert b"user" in err


def test_stdout_carries_only_protocol_messages(generated_dir: Path, tmp_path: Path) -> None:
    """A real session over stdio: every stdout line is JSON-RPC; the call is audited."""
    audit = tmp_path / "audit.jsonl"
    process = _start(
        ["--user", "cust_uk_01", "--audit-log", str(audit)], _server_env(generated_dir)
    )
    assert process.stdin and process.stdout
    lines: list[bytes] = []

    def send(message: dict, reply: bool = True) -> None:
        process.stdin.write((json.dumps(message) + "\n").encode())  # type: ignore[union-attr]
        process.stdin.flush()  # type: ignore[union-attr]
        if reply:
            lines.append(process.stdout.readline())  # type: ignore[union-attr]

    try:
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": LATEST_PROTOCOL_VERSION, "capabilities": {},
            "clientInfo": {"name": "test", "version": "0"}}})  # fmt: skip
        send({"jsonrpc": "2.0", "method": "notifications/initialized"}, reply=False)
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        send({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
              "params": {"name": "get_order", "arguments": {"order_id": "VC-10002"}}})  # fmt: skip
        process.stdin.close()
        rest, err = process.communicate(timeout=120)
    finally:
        if process.poll() is None:
            process.kill()
    lines += rest.splitlines(keepends=True)
    messages = [json.loads(line) for line in lines if line.strip()]
    assert len(messages) == 3 and all(m.get("jsonrpc") == "2.0" for m in messages)
    assert [t["name"] for t in messages[1]["result"]["tools"]] == list(APPROVAL_TOOL_SPECS)
    order = json.loads(messages[2]["result"]["content"][0]["text"])
    assert order["order"]["order_id"] == "VC-10002"
    logged = audit.read_text(encoding="utf-8")
    assert '"via": "mcp"' in logged and '"tool": "get_order"' in logged
    assert "gsk_dummy_key" not in logged and "dummy-customer-key" not in logged
    assert b"gsk_dummy_key" not in err

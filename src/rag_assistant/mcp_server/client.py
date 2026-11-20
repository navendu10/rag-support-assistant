"""The agent's side of MCP (Session 12, v10): load the tools from the server, call them.

``MCPToolClient`` keeps **one** MCP client session per user open (the server is started
for that user on first use and stopped on ``close``). The sessions live on a background
event loop, because ``langchain-mcp-adapters`` tools are async and the agent graph is
synchronous; each session is held by its own task, so it is opened and closed in the
same task, as anyio requires.

``mcp_tool_factory(client)`` gives the agent graph the tools for one ``AgentSession``:
synchronous LangChain tools with the **loaded** names, descriptions and schemas
(``additionalProperties`` dropped, so the model is given exactly v9's tool definitions).
Each call:

1. validates the arguments with the local Pydantic model (the same errors as v9, and an
   invalid call never reaches the server);
2. calls the server and parses its JSON text (the same JSON as the in-process tool);
3. keeps the turn's state in the agent, as v9 does: knowledge passages are numbered K1,
   K2... across the turn (the stateless server numbers each call from K1) and kept for
   the guard; the retrieval context from the structured result goes to
   ``session.contexts``; a draft becomes a ``Draft`` with the chat's thread id (one
   drafted for anyone but the session's user is dropped);
4. records the call in ``session.calls``.

The server writes the tool-call audit events (``via: "mcp"``); the agent still writes
pause, approve, reject, submission and refusal.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import threading
import time
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool, StructuredTool
from langchain_mcp_adapters.tools import load_mcp_tools
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from rag_assistant.agent.knowledge import RetrievalContext
from rag_assistant.agent.tickets import Draft
from rag_assistant.agent.tools import APPROVAL_TOOL_SPECS, AgentSession, ToolCall
from rag_assistant.retrieval.vector import RetrievedChunk
from rag_assistant.schemas import User

KNOWLEDGE_TOOL = "search_knowledge_base"
DRAFT_TOOL = "draft_ticket"
CALL_TIMEOUT_S = 300.0  # a knowledge call runs the whole v7 pipeline

SessionOpener = Callable[[User], AbstractAsyncContextManager[ClientSession]]


class MCPToolError(RuntimeError):
    """The server reported an error for a tool call."""


@dataclass(frozen=True)
class RemoteTool:
    """One tool as the server lists it."""

    name: str
    description: str
    input_schema: dict


def stdio_opener(stage: str, audit_log: Path | None) -> SessionOpener:
    """Open a session with ``python -m rag_assistant.mcp_server --user <id> --client agent``."""

    @asynccontextmanager
    async def opener(user: User) -> AsyncIterator[ClientSession]:
        args = ["-m", "rag_assistant.mcp_server", "--user", user.user_id, "--stage", stage]
        args += ["--client", "agent"]
        if audit_log is not None:
            args += ["--audit-log", str(audit_log)]
        params = StdioServerParameters(command=sys.executable, args=args, env=dict(os.environ))
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            yield session

    return opener


class MCPToolClient:
    """One open MCP session per user, on a background event loop."""

    def __init__(self, opener: SessionOpener, audit_log: Path | None = None) -> None:
        """``opener`` opens a session for a user (stdio in production, memory in tests)."""
        self._opener = opener
        self.audit_log = audit_log
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._tools: dict[str, dict[str, BaseTool]] = {}
        self._stops: dict[str, asyncio.Event] = {}
        self._tasks: list[asyncio.Future] = []

    @classmethod
    def for_stage(cls, stage: str, audit_log: Path | None) -> MCPToolClient:
        """The production client: a stdio server process per user, for ``stage``."""
        return cls(stdio_opener(stage, audit_log), audit_log)

    @classmethod
    def for_evaluation(cls, stage: str) -> MCPToolClient:
        """A client whose servers audit to a temporary file (evaluation writes nothing)."""
        path = Path(tempfile.mkdtemp(prefix="mcp-audit-")) / "audit.jsonl"
        return cls.for_stage(stage, path)

    def _run(self, coro: Any, timeout: float = CALL_TIMEOUT_S) -> Any:
        """Run ``coro`` on the background loop and wait for its result."""
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout)

    async def _hold(self, user: User, ready: asyncio.Future, stop: asyncio.Event) -> None:
        """Open the session, load its tools, and keep it open until ``stop``."""
        try:
            async with self._opener(user) as session:
                tools = await load_mcp_tools(session)
                ready.set_result({t.name: t for t in tools})
                await stop.wait()
        except BaseException as exc:  # noqa: BLE001 - reported to the waiting caller
            if not ready.done():
                ready.set_exception(exc)
            else:
                raise

    async def _open(self, user: User) -> dict[str, BaseTool]:
        """Start holding a session for ``user``; return its tools once loaded."""
        ready: asyncio.Future = self._loop.create_future()
        stop = asyncio.Event()
        self._stops[user.user_id] = stop
        self._tasks.append(asyncio.ensure_future(self._hold(user, ready, stop)))
        return await ready

    def tools(self, user: User) -> dict[str, BaseTool]:
        """The adapter's tools for ``user``'s session (opened on first use)."""
        if user.user_id not in self._tools:
            self._tools[user.user_id] = self._run(self._open(user), timeout=120.0)
        return self._tools[user.user_id]

    def listed(self, user: User) -> list[RemoteTool]:
        """The tools as the server lists them (name, description, input schema)."""
        return [
            RemoteTool(t.name, t.description, dict(t.args_schema))  # type: ignore[arg-type]
            for t in self.tools(user).values()
        ]

    def call(self, user: User, name: str, args: dict) -> tuple[str, dict | None]:
        """Call ``name`` for ``user``: ``(the JSON text, the structured content)``."""
        tool = self.tools(user)[name]
        call = {"name": name, "args": args, "id": f"mcp-{time.time_ns()}", "type": "tool_call"}
        message = self._run(tool.ainvoke(call))
        assert isinstance(message, ToolMessage)
        content = message.content
        text = content if isinstance(content, str) else "".join(
            block.get("text", "") if isinstance(block, dict) else str(block) for block in content
        )  # fmt: skip
        if message.status == "error":
            raise MCPToolError(text)
        artifact = message.artifact or {}
        structured = artifact.get("structured_content") if isinstance(artifact, dict) else None
        return text, structured

    def close(self) -> None:
        """Stop every server and the background loop."""

        async def stop_all() -> None:
            for stop in self._stops.values():
                stop.set()
            if self._tasks:
                await asyncio.gather(*self._tasks, return_exceptions=True)

        if self._loop.is_running():
            try:
                self._run(stop_all(), timeout=30.0)
            finally:
                self._loop.call_soon_threadsafe(self._loop.stop)
                self._thread.join(timeout=10.0)


# ------------------------------------------------------------- the agent's tools


def model_schema(input_schema: dict) -> dict:
    """The schema to bind for the model: the server's, without ``additionalProperties``
    (LangChain drops it from a Pydantic model, so v9 and v10 give the model the same
    definitions). The server still refuses unknown fields."""
    return {k: v for k, v in input_schema.items() if k != "additionalProperties"}


def _passage_chunk(passage: dict, cited: list[RetrievedChunk], index: int) -> RetrievedChunk:
    """The guard's chunk for one returned passage (from the structured context if sent)."""
    if index < len(cited):
        return cited[index]
    return RetrievedChunk(
        passage["text"], passage["doc_id"], passage["title"], 0, "public", "ALL", 0.0,
        trust=passage.get("trust", "store"),
    )  # fmt: skip


def _keep_turn_state(
    session: AgentSession, name: str, result: dict, structured: dict | None
) -> None:
    """What v9's in-process tools keep in the session: passages, contexts, drafts."""
    if name == KNOWLEDGE_TOOL:
        retrieval = (structured or {}).get("retrieval")
        context = RetrievalContext.from_dict(retrieval) if retrieval else None
        if context is not None:
            session.contexts.append(context)
        cited = context.cited if context is not None else []
        for n, passage in enumerate(result.get("passages", [])):
            session.passages.append(_passage_chunk(passage, cited, n))
            passage["id"] = f"K{len(session.passages)}"
    elif name == DRAFT_TOOL and result.get("status") == "draft":
        ticket = result["ticket"]
        if ticket.get("drafted_by") == session.user.user_id:
            fields = {k: ticket[k] for k in Draft.__dataclass_fields__ if k != "thread_id"}
            session.drafts.append(Draft(thread_id=session.thread_id, **fields))


def _remote_tool(client: MCPToolClient, session: AgentSession, tool: BaseTool) -> StructuredTool:
    """A synchronous tool for the graph that calls ``tool`` over MCP for ``session``."""
    name = tool.name
    model = APPROVAL_TOOL_SPECS[name][0]

    def run(**kwargs: object) -> str:
        start = time.perf_counter()
        args = model.model_validate(kwargs).model_dump()
        text, structured = client.call(session.user, name, args)
        result = json.loads(text)
        _keep_turn_state(session, name, result, structured)
        ms = round((time.perf_counter() - start) * 1000, 1)
        session.calls.append(ToolCall(name, args, result, ms))
        return json.dumps(result, ensure_ascii=False)

    return StructuredTool.from_function(
        func=run,
        name=name,
        description=tool.description,
        args_schema=model_schema(dict(tool.args_schema)),  # type: ignore[arg-type]
    )


def mcp_tool_factory(client: MCPToolClient) -> Callable[[AgentSession], list[StructuredTool]]:
    """The agent graph's tool factory for v10: the server's tools, bound to the session."""

    def factory(session: AgentSession) -> list[StructuredTool]:
        return [_remote_tool(client, session, t) for t in client.tools(session.user).values()]

    return factory

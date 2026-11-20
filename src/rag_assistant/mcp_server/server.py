"""The agent's tools as an MCP server (Session 12), for one signed-in user per process.

Run (stdio transport; the client starts this process)::

    python -m rag_assistant.mcp_server --user cust_uk_01 [--stage v10] [--audit-log PATH]

**Tools.** The six tools of v9 (``agent/tools.py``'s ``APPROVAL_TOOL_SPECS``): the same
names, descriptions and Pydantic input models (the advertised input schema is the model's
JSON schema, and the arguments are validated by that model, so an unknown field such as
``user_id`` is refused). Each call runs the existing tool function; there is no other
logic here. There is **no** submit, approve or admin tool, and no resources or prompts.

**Identity.** The user comes only from ``--user`` or ``VOLTCART_MCP_USER``, checked
against ``data/users.json``; the server refuses to start without a valid one. Role,
region and the Weaviate key follow that profile; keys are read from ``.env`` by the
settings, never from the client.

**Stateless calls.** Each call gets a fresh ``AgentSession``: nothing carries over between
calls or clients. The agent (v10) keeps the turn's state itself (``mcp_server/client.py``).

**Results.** The text content is exactly what the in-process tool returns (the same JSON),
so a model sees the same tool messages over MCP. The structured content carries
``{"result": ...}`` and, for ``search_knowledge_base``, ``"retrieval"``: the retrieval
context (chunks, doc ids, trust, the rewrite) the evaluation needs.

**Drafts** made here can't be submitted: there is no approval channel over MCP. For
external clients (Claude Code, MCP Inspector) each draft says so; only the v10 agent
starts the server with ``--client agent``, because its own chat holds the approval step.

**stdio hygiene.** stdout carries protocol messages only: the real stdout is handed to the
transport, and ``sys.stdout`` is pointed at stderr before anything else is imported or
run, so a stray ``print`` can't corrupt the stream. Logs go to stderr; every tool call is
written to the audit log (``via: "mcp"``).
"""

from __future__ import annotations

import argparse
import inspect
import json
import logging
import os
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from io import TextIOWrapper
from pathlib import Path
from typing import Annotated, Any, BinaryIO

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.utilities.func_metadata import ArgModelBase
from mcp.types import CallToolResult, TextContent
from pydantic import BaseModel, create_model

from rag_assistant.agent.audit import AuditLog, FileAudit, tool_fields
from rag_assistant.agent.knowledge import RetrievalContext, make_knowledge
from rag_assistant.agent.orders import OrderStore
from rag_assistant.agent.rules import BusinessRules, load_business_rules
from rag_assistant.agent.tools import (
    APPROVAL_TOOL_SPECS,
    AgentSession,
    KnowledgeResult,
)
from rag_assistant.schemas import Product, User, load_catalogue, load_users

SERVER_NAME = "voltcart-support"
USER_ENV_VAR = "VOLTCART_MCP_USER"
KNOWLEDGE_TOOL = "search_knowledge_base"
DRAFT_TOOL = "draft_ticket"
EXTERNAL_DRAFT_NOTE = "This draft can't be submitted from here; use the support chat."
logger = logging.getLogger(__name__)

KnowledgeFactory = Callable[[User, list[RetrievalContext]], Callable[[str], KnowledgeResult]]


@dataclass
class Backend:
    """Everything a tool call needs, built once per server process for its one user."""

    user: User
    orders: OrderStore
    rules: BusinessRules
    catalogue: dict[str, Product]
    knowledge_factory: KnowledgeFactory  # builds the v7 pipeline lazily, on first use
    audit: AuditLog | None = None
    external: bool = True  # drafts carry EXTERNAL_DRAFT_NOTE
    session_id: str = field(default_factory=lambda: f"mcp-{uuid.uuid4().hex[:12]}")
    closers: list[Callable[[], None]] = field(default_factory=list)  # run on shutdown

    def close(self) -> None:
        """Release the knowledge pipeline (Weaviate clients) and the order database."""
        for close in self.closers:
            close()
        self.orders.close()

    def call(self, name: str, arguments: dict[str, Any]) -> CallToolResult:
        """Validate ``arguments`` with the tool's model, run the tool, return text and data."""
        model, logic, _description = APPROVAL_TOOL_SPECS[name]
        args = model.model_validate(arguments).model_dump()
        contexts: list[RetrievalContext] = []
        session = AgentSession(
            self.user,
            self.orders,
            self.rules,
            self.catalogue,
            lambda q: self.knowledge_factory(self.user, contexts)(q),
            approval=True,
            thread_id=self.session_id,
        )
        start = time.perf_counter()
        result = logic(session, **args)
        if name == DRAFT_TOOL and self.external and result.get("status") == "draft":
            result = {**result, "note": EXTERNAL_DRAFT_NOTE}
        structured: dict[str, Any] = {"result": result}
        if name == KNOWLEDGE_TOOL and contexts:
            structured["retrieval"] = contexts[-1].to_dict()
        if self.audit is not None:
            self.audit.write(
                "tool_call",
                self.user.user_id,
                self.session_id,
                via="mcp",
                ms=round((time.perf_counter() - start) * 1000, 1),
                **tool_fields(name, args, result),
            )
        text = json.dumps(result, ensure_ascii=False)
        return CallToolResult(
            content=[TextContent(type="text", text=text)], structuredContent=structured
        )


def _strict_args(name: str, model: type[BaseModel]) -> type[ArgModelBase]:
    """FastMCP's argument model for ``model``'s fields, refusing unknown fields."""
    fields = {f: (info.annotation, info) for f, info in model.model_fields.items()}
    args = create_model(f"{name}_arguments", __base__=ArgModelBase, **fields)  # type: ignore[call-overload]
    args.model_config["extra"] = "forbid"
    args.model_rebuild(force=True)
    return args


def build_server(backend: Backend) -> FastMCP:
    """A FastMCP server with the six tools, each a thin wrapper over ``backend.call``.

    The advertised input schema is the tool's own Pydantic model's JSON schema, and
    FastMCP's argument model is replaced by a strict copy of it (unknown fields refused).
    Both use FastMCP's tool manager, so ``mcp`` is pinned exactly (ADR 0010).
    """
    server = FastMCP(SERVER_NAME, log_level="WARNING")
    for name, (model, _logic, description) in APPROVAL_TOOL_SPECS.items():
        tool = _wrapper(backend, name, model)
        added = server._tool_manager.add_tool(tool, name=name, description=description)  # noqa: SLF001
        added.parameters = model.model_json_schema()
        added.fn_metadata.arg_model = _strict_args(name, model)
    return server


def _wrapper(backend: Backend, name: str, model: type[BaseModel]) -> Callable[..., CallToolResult]:
    """The function FastMCP registers for ``name``: its signature is the model's fields."""

    def tool(**arguments: Any) -> CallToolResult:
        return backend.call(name, arguments)

    params = [
        inspect.Parameter(
            f,
            inspect.Parameter.KEYWORD_ONLY,
            annotation=Annotated[info.annotation, info],
            default=inspect.Parameter.empty if info.is_required() else info.default,
        )
        for f, info in model.model_fields.items()
    ]
    tool.__signature__ = inspect.Signature(params, return_annotation=CallToolResult)  # type: ignore[attr-defined]
    tool.__name__ = name
    return tool


# ------------------------------------------------------------------- startup


def resolve_user(user_id: str | None, users_file: Path) -> User:
    """The signed-in user from ``--user`` / ``VOLTCART_MCP_USER``; ``SystemExit(2)`` if invalid."""
    user_id = user_id or os.environ.get(USER_ENV_VAR)
    if not user_id:
        print(f"error: no user: pass --user or set {USER_ENV_VAR}", file=sys.stderr)
        raise SystemExit(2)
    users = {u.user_id: u for u in load_users(users_file)}
    if user_id not in users:
        print(f"error: unknown user {user_id!r} (not in {users_file.name})", file=sys.stderr)
        raise SystemExit(2)
    return users[user_id]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the server's command line."""
    parser = argparse.ArgumentParser(prog="python -m rag_assistant.mcp_server")
    parser.add_argument("--user", help=f"user_id from data/users.json (or {USER_ENV_VAR})")
    parser.add_argument("--stage", default="v10", help="the stage whose knowledge pipeline runs")
    parser.add_argument("--audit-log", type=Path, help="audit JSONL (default: from config)")
    parser.add_argument(
        "--client",
        choices=["external", "agent"],
        default="external",
        help="agent: started by the v10 agent, whose chat approves drafts",
    )
    return parser.parse_args(argv)


def backend_from_settings(args: argparse.Namespace) -> Backend:
    """The backend for the command line's user, from the project's settings."""
    from rag_assistant.config import get_settings, load_stage
    from rag_assistant.graph.handoff import MemoryTicketStore
    from rag_assistant.pipeline import Pipeline

    settings = get_settings()
    user = resolve_user(args.user, settings.paths.resolve(settings.paths.users_file))
    stage = load_stage(args.stage)
    generated = settings.paths.resolve(settings.paths.generated_dir)
    pipelines: list[Pipeline] = []

    def knowledge(u: User, sink: list[RetrievalContext]) -> Callable[[str], KnowledgeResult]:
        if not pipelines:  # the v7 pipeline (Weaviate, models) is built on first use
            pipelines.append(Pipeline.from_stage(stage, settings, MemoryTicketStore()))
        return make_knowledge(pipelines[0], u, sink)

    audit_path = args.audit_log or settings.paths.resolve(settings.approval.audit_path)
    return Backend(
        user=user,
        orders=OrderStore(generated / "orders.db"),
        rules=load_business_rules(),
        catalogue={p.code: p for p in load_catalogue(generated / "manifest.json")},
        knowledge_factory=knowledge,
        audit=FileAudit(audit_path),
        external=args.client == "external",
        closers=[lambda: [p.close() for p in pipelines]],
    )


def run_stdio(server: FastMCP, protocol_out: BinaryIO) -> None:
    """Serve over stdio, writing protocol messages to ``protocol_out`` only."""
    import anyio
    from mcp.server.stdio import stdio_server

    async def serve() -> None:
        stdout = anyio.wrap_file(TextIOWrapper(protocol_out, encoding="utf-8"))
        async with stdio_server(stdout=stdout) as (read, write):
            low = server._mcp_server  # noqa: SLF001 - FastMCP's own stdio runner, with our stdout
            await low.run(read, write, low.create_initialization_options())

    anyio.run(serve)


def main(argv: list[str] | None = None) -> int:
    """Start the server for one user; exit 2 if the user is missing or unknown."""
    protocol_out = sys.stdout.buffer
    sys.stdout = sys.stderr  # anything that prints goes to stderr, never into the protocol
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    args = parse_args(argv)
    backend = backend_from_settings(args)
    logger.warning("MCP server for %s (%s) started", backend.user.user_id, backend.user.role)
    try:
        run_stdio(build_server(backend), protocol_out)
    finally:
        backend.close()
    return 0

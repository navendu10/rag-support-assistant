# ADR 0010: The agent's tools as an MCP server (stdio, one user per process, no writes)

- **Status:** Accepted
- **Date:** 2026-11-19
- **Versions:** `mcp` 1.30.0 (FastMCP and the stdio transport) and `langchain-mcp-adapters`
  0.3.2.

## Context

The agent's tools (ADR 0008) are useful beyond our chat. An MCP client such as Claude
Code could look up a customer's orders, warranty and return windows, or search the
knowledge base. MCP is the standard way to offer tools to such clients. The risks are
the same as for the agent, and wider:

- an unknown client chooses the calls;
- identity has to come from somewhere;
- a write over MCP would bypass the approval step (ADR 0009).

## Decision

1. **One server, the same tools.** `mcp_server/server.py` (FastMCP) registers v9's six
   tools from `APPROVAL_TOOL_SPECS`: the same names, descriptions and Pydantic input
   models. Each call validates its arguments with the model, which refuses unknown
   fields such as `user_id`, and runs the existing function. No tool logic is
   duplicated, and there are no resources, prompts or admin functions.
2. **stdio, one user per process.**
   - The user comes only from `--user` or `VOLTCART_MCP_USER`, checked against
     `data/users.json`. Without a valid user the server exits with code 2.
   - Role, region and the Weaviate key follow that profile. Keys come from `.env` and
     never from the client.
   - To serve another user, start another process.
3. **No writes over MCP.**
   - `draft_ticket` only drafts.
   - For external clients each draft says *"This draft can't be submitted from here;
     use the support chat."* A draft id from an MCP session is refused by a chat's
     `/approve`, because it was never shown in that chat (a test checks this).
   - Submitting stays in the agent, behind the approval step.
4. **Stateless calls.**
   - Each call gets a fresh `AgentSession`.
   - The v10 agent keeps the turn's state itself: it numbers passages K1, K2… across the
     turn, rebuilds drafts with its own thread id, and records the calls.
5. **The same text, more data.**
   - The text content is exactly the in-process tool's JSON, so a model sees identical
     tool messages and the agent's guard works unchanged.
   - The structured content adds `{"result", "retrieval"}`: for the knowledge tool, the
     chunks, doc ids and trust levels it used. v10's evaluation rows get recall and leak
     metrics from it.
6. **stdio hygiene.**
   - The real stdout is handed to the transport, and `sys.stdout` points at stderr, so a
     stray `print` can't corrupt the protocol.
   - Logs go to stderr. Every call is audited (`via: "mcp"`).
7. **The agent over MCP (v10).**
   - `tools_via_mcp: true` loads the tools with `langchain-mcp-adapters` over one
     persistent session per user, held on a background event loop. The adapter's tools
     are async-only, and its default mode starts a process per call.
   - The agent binds the loaded schemas with `additionalProperties` dropped, so the
     model is given byte-identical tool definitions to v9's (a test checks this).
   - Approval, the guard, the step limit and submission stay in the agent.

## Alternatives considered

- **HTTP transport with sign-in.** It suits remote clients and many users, but it needs
  authentication (OAuth), per-request identity and rate limits. Future work: until then,
  stdio's "the person who starts the process is the user" is the honest identity model.
- **A `user_id` argument on each tool.** One prompt injection away from reading someone
  else's orders, as in ADR 0008. Identity stays out of the tool arguments.
- **A stateful server** (one session for the whole conversation). It would carry passages
  and drafts across turns and across clients. Stateless calls are simpler to reason
  about.
- **The low-level MCP `Server` instead of FastMCP.** It gives full control of schemas
  and validation, but it is more code. FastMCP was used, with two small reaches into its
  tool manager, which is why `mcp` is pinned exactly:
  - the advertised schema is set to the model's own;
  - its argument model is a strict copy that refuses extra fields.
- **`mcp` 2.x.** `langchain-mcp-adapters` 0.3.2 requires `mcp<2`. Upgrade both together
  when the adapter supports 2.x, and re-run `tests/unit/test_mcp.py`, which covers the
  tool-manager reach-ins.

## Consequences

- **Startup.** Each user's server is a separate process. The v7 pipeline is built lazily
  on the first knowledge call, so a process starts in a few seconds and the order tools
  need no network.
- **Audit.** The server writes the tool-call events (with its own session id), and the
  agent writes pause, decision and submission events. Joining them by conversation needs
  the timestamps.
- **Errors differ in wording.** An invalid call from an external client gets the SDK's
  "Error executing tool …" message. The v10 agent validates arguments locally first, so
  its errors are the same as v9's.

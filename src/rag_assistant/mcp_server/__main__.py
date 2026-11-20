"""``python -m rag_assistant.mcp_server --user <user_id>``: the agent's tools over MCP (stdio)."""

from __future__ import annotations

import sys

from rag_assistant.mcp_server.server import main

if __name__ == "__main__":
    sys.exit(main())

# Demo: the VoltCart tools in Claude Code (MCP)

The MCP server (`python -m rag_assistant.mcp_server`) exposes the agent's six tools to any
MCP client over stdio. The server serves **one user**, fixed when it starts. This page
connects it to Claude Code in VS Code, and optionally to MCP Inspector.

## Before you start

- **Setup:** the project is set up (`docs/setup.md`): `.venv`, `pip install -e .`, and
  `python scripts/generate_data.py`.
- **For `search_knowledge_base`:** Weaviate is running (`docker compose up -d`), the v7
  collections are ingested, `python scripts/setup_access.py` has written the role keys to
  `.env`, and `GROQ_API_KEY` is in `.env`. The order tools work without any of these.
- **Keys:** they stay in `.env`. The server reads them itself; they never go in a client
  config.

## Connect it to Claude Code (VS Code)

1. Copy `.mcp.json.example` to `.mcp.json` in the project root. `.mcp.json` is
   git-ignored because it holds your local paths.
2. Replace `C:/<your folder>` with the folder that contains the project, so `command` is
   the absolute path to the venv's Python:

   ```json
   {
     "mcpServers": {
       "voltcart-support": {
         "type": "stdio",
         "command": "C:/Users/you/projects/rag-support-assistant/.venv/Scripts/python.exe",
         "args": ["-m", "rag_assistant.mcp_server", "--user", "cust_uk_01"]
       }
     }
   }
   ```

3. Choose the signed-in customer with `--user`: `cust_uk_01`, `cust_us_01`, `cust_in_01`,
   or the staff user `agent_01` (all from `data/users.json`). An unknown user stops the
   server with exit code 2.
4. Open the project in VS Code with the Claude Code extension and start a new
   conversation. Claude Code asks you to approve the project's MCP server the first time.
   Approve it, then run `/mcp` to check that `voltcart-support` is connected with six
   tools.
5. Try:
   - "Which orders do I have?" (`list_my_orders`)
   - "Is the TV from VC-10001 still under warranty?" (`check_warranty`)
   - "Can I still return the AeroBuds from VC-10002?" (`check_return_eligibility`)
   - "What is order VC-20001?" (another customer's order: "not found", like a missing one)
   - "How do I factory reset the XR-2200?" (`search_knowledge_base`, needs Weaviate and
     Groq)
   - "Draft a return ticket for VC-10002" (`draft_ticket`: a draft only)

## Verified (2026-11-20)

Run in Claude Code in VS Code, with `.mcp.json` from the example and `--user cust_uk_01`:

- **Connection:** Claude Code connected to `voltcart-support` and listed the six tools.
- **A policy question:** "What's the return window for opened earbuds in the UK?" came
  back as 15 days, with all ear tips, cables and accessories included. The answer came
  from `search_knowledge_base`, citing the current UK returns policy.
- **A warranty question:** "Which of my orders are still under warranty?" went through
  `list_my_orders` and then `check_warranty` for each order. All three are covered: the
  TV's panel until 2028-03-12, and the earbuds until 2027-10-20 and 2027-10-01.
- **Another customer's order** came back "not found", exactly like a missing one.
- **"Submit a ticket"** found no tool to submit with: Claude Code could only offer a
  draft, and pointed to the support chat for approval.

## What you'll see

- **Every result is JSON**, the same JSON the v9 agent's tools return.
- **Drafts.** A draft says: *"This draft can't be submitted from here; use the support
  chat."* There is no submit or approve tool over MCP. A draft id from Claude Code is
  refused by `/approve` in `scripts/chat.py`, because it was never shown in that chat.
- **No user arguments.** No tool takes a user or customer id, and an extra argument such
  as `user_id` is refused. To act as someone else, restart the server with another
  `--user`.
- **Audit log.** Every call is appended to `data/audit/audit.jsonl` with `"via": "mcp"`.
  Knowledge results are logged as doc ids only.
- **Errors.** Server errors and logs go to stderr. Claude Code shows them in its MCP
  logs.

## Optional: MCP Inspector (Node.js)

MCP Inspector is a browser UI for calling a server's tools by hand. It needs Node.js 18
or later:

```powershell
npx @modelcontextprotocol/inspector C:\Users\you\projects\rag-support-assistant\.venv\Scripts\python.exe -m rag_assistant.mcp_server --user cust_uk_01
```

1. Open the URL it prints.
2. Choose **Connect**, then **Tools → List Tools**.
3. Pick a tool, fill in its arguments, and choose **Run Tool**.

The **Structured content** panel shows `{"result": ...}`. For `search_knowledge_base` it
also shows `"retrieval"`, the chunks, doc ids and trust levels the answer was based on.

## Troubleshooting

| Symptom | Cause |
|---|---|
| The server exits at once | the user is missing or unknown: check `--user` |
| `orders.db missing` | run `python scripts/generate_data.py` |
| `search_knowledge_base` errors | Weaviate isn't running, or `.env` lacks the role keys or `GROQ_API_KEY` |
| Claude Code shows no tools | `command` isn't the absolute path to the venv's `python.exe` |

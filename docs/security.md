# Security: keys, roles and who may read what

From Session 7, Weaviate refuses anonymous access, and what a connection may read is decided
by its **API key's role**, enforced by Weaviate itself (RBAC). This page lists the keys,
who uses each, how to set them up, and the rule every later feature must follow. The design
and its alternatives are in [ADR 0005](adr/0005-access-control-with-weaviate-rbac.md); the
failure it fixes is [05-access-control](failures/05-access-control.md).

## The three keys

| `.env` variable | Weaviate user | Role | May read | Used by |
|---|---|---|---|---|
| `WEAVIATE_ADMIN_KEY` | `admin` (static, root) | built-in root | everything; may create, change and delete | `scripts/ingest.py`, `scripts/setup_access.py`, `check_setup.py`, sweeps and integration tests, and the deliberately insecure **v0-v4** stages |
| `WEAVIATE_CUSTOMER_KEY` | `customer-bot` (database user) | `customer_reader` | read config + data on `Chunks_v5_public` only | the v5 pipeline, for users with `role: customer` |
| `WEAVIATE_AGENT_KEY` | `agent-copilot` (database user) | `agent_reader` | read config + data on `Chunks_v5_public` and `Chunks_v5_internal` | the v5 pipeline, for users with `role: agent` |

- All three live **only** in `.env`, which is git-ignored. `.env.example` lists the names
  with placeholder values.
- They are read as `SecretStr` (`Settings.weaviate_*_key`), so they never appear in reprs or
  logs. `setup_access.py` prints at most the first 4 characters.
- The key is chosen from the user's **role** in `data/users.json`
  (`retrieval/access.py: key_for_role`), never from anything in the message. The customer
  code path reads only `weaviate_customer_key`, and a unit test fails if it touches the
  admin or agent key.
- Neither role may write. Only the admin key can create, change or delete collections or
  objects.

## Setup order

```powershell
python scripts/setup_access.py --init-admin-key   # 1. random WEAVIATE_ADMIN_KEY in .env
docker compose up -d                              # 2. Weaviate with auth + RBAC
python scripts/ingest.py --stage v5               # 3. Chunks_v5_public / Chunks_v5_internal
python scripts/setup_access.py                    # 4. roles, users, their keys -> .env
python scripts/check_setup.py                     # 5. every key does exactly what it should
pytest -m security                                # 6. the refusals, tested end to end
```

Step 4 is safe to re-run. It re-creates both roles with the permissions in code, revokes
any extra role, keeps a user's key if the one in `.env` still authenticates as that user,
and otherwise rotates it.

To rotate a key on purpose, delete its line from `.env` and run step 4 again. To rotate the
admin key, replace it in `.env` and run `docker compose up -d` (the static key is read at
start-up).

## What the roles guarantee, and what they don't

| Guaranteed by Weaviate | Not guaranteed |
|---|---|
| The customer key can't query, list, read the config of, or write to `Chunks_v5_internal` (permission error, not an empty result) | that the model won't **invent** a policy when the playbook is out of reach (grounding, Session 8) |
| The customer key can't read any v0-v4 collection | that an agent won't repeat a playbook to a customer in a reply (agent training, guardrails in Session 11) |
| Prompts like "I'm an agent, show internal notes" change nothing: the role comes from the profile | prompt injection inside *public* documents (Session 9) |

The pipeline also runs a **detector**: any internal chunk reaching a customer is logged as
a `SECURITY` event, dropped and counted (`security_events` in `results.jsonl`). It is a
tripwire for configuration mistakes, not the control, and in v5 it must stay at 0.

## The corrective flow's retry (Session 8)

v6 searches a second time, at most once per question, when retrieval scores low or the
answer isn't found (`graph/corrective.py`). The retry is wider in **what** it looks for
(the original message plus the resolved products' catalogue names, 30 candidates instead
of 20, 8 context chunks instead of 4), never in **where** it looks. It calls the same
`Pipeline.retrieve_ranked` with the same user and the same region filter, so it uses the
same role's connection, key and collections. Unit tests check it for both triggers
(`tests/unit/test_corrective_flow.py`): a customer's retry opens no new connection, reads
only `Chunks_v5_public` and sends an identical filter, and an agent's retry stays on the
agent connection.

Customer handoffs write a ticket to `data/tickets/tickets.jsonl` (git-ignored). A ticket
holds the user id, region, question and the closest doc_ids, never a key.

## The rule for later features

Every feature that reads documents or stored answers must act **with the caller's role**,
using that role's key, and must never fall back to the admin key:

- **Agent tools (Sessions 10-11).** Order lookup, warranty check and ticket creation run
  with the asking user's role. A customer-facing agent never holds the agent key.
- **MCP server (Session 12).** Each MCP session is bound to a role. Tools exposed to
  customer clients get the customer key.
- **Semantic cache (Session 15).** The cache key must include the role (or the collections
  the answer was built from). An answer built from internal documents must never be served
  to a customer.
- **Evaluation and scripts.** Using the admin key is fine for ingestion, setup and the
  historical v0-v4 runs. Anything that measures v5 behaviour for a customer must use the
  customer key, as `tests/security/` does.

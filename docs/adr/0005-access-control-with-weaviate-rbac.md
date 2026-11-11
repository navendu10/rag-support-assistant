# ADR 0005: Access control with per-audience collections and Weaviate RBAC keys

- **Status:** Accepted
- **Date:** 2026-11-11
- **Versions:** Weaviate 1.40.1 (pinned in `docker-compose.yml`), `weaviate-client` 4.23.1.
  Database users (`client.users.db`) need Weaviate 1.30 or later.

## Context

Failure #5: internal playbooks (goodwill credit limits, fraud checks, refund exceptions,
escalation rules) reached customers in every stage from v0 to v4. Session 6 showed why
filtering and ranking can't fix it:

- In v4 the region filter **freed candidate slots**, and g020's refund-exceptions playbook
  (region ALL) took one. The reranker scored it 0.77, so it reached rank 1 of the context.
- In v4a a query rewrite ("Networking price match") pulled g023's fraud-checks playbook
  into the context.
- The cross-encoder rates playbooks as highly relevant, because they are.

Every part of retrieval that decides *what is relevant* can be changed by the question:
the rewriter, the search, the reranker, a filter built from the wrong field. A control
that lives inside that chain is only as strong as its weakest link. Customers must be
**unable** to read internal documents, and agents must keep full access.

## Decision

1. **Separate collections by audience.** Stages with `access_control` (v5 on) ingest the
   same chunks as v2 into `Chunks_v5_public` and `Chunks_v5_internal`. A unit test proves
   the two together hold exactly v2's chunk texts.
2. **Weaviate authentication and RBAC.**
   - Anonymous access is off. One static root user, `admin`, has its key in `.env`.
   - Two roles: `customer_reader` (read config and data on `Chunks_v5_public` only) and
     `agent_reader` (both collections).
   - Two database users, `customer-bot` and `agent-copilot`, each with exactly one role.
     They are created by `scripts/setup_access.py`, which is safe to re-run.
3. **The key follows the user's role** in `data/users.json`, never the message:
   - Customers connect with the customer key and search the public collection only.
   - Agents connect with the agent key, search both collections and merge the lists with
     reciprocal rank fusion before reranking.
   - Only ingestion, `setup_access.py` and the v0-v4 "before" stages use the admin key.
4. **A detector, not a control.** If an internal chunk ever reached a customer, the
   pipeline would log a SECURITY event, drop the chunk and count it (`security_events`).
   With the roles in place it never fires.

## Alternatives considered

| Option | Why not |
|---|---|
| One collection plus `audience = public` filter for customers | The filter is built and applied by application code on every query path. A missed path, a bug in a new feature (a semantic cache, an agent tool), or a filter built from the wrong field leaks everything, and Weaviate can't tell. Session 6's region filter is exactly this kind of control. |
| Application-level check after retrieval (drop internal chunks) | The internal text has already been read by the application, and the control depends on every caller remembering it. Kept only as the detector. |
| Multi-tenancy (a tenant per audience) | Tenants isolate data well, but RBAC is still needed to stop the customer key from reading the internal tenant, and tenant management adds a moving part for two fixed audiences. Collections plus collection-scoped roles express the same thing more plainly. |
| Separate Weaviate instances | The strongest isolation, but it doubles operations for a local demo. Collection-level RBAC gives database-enforced refusal on one instance. |
| OIDC instead of API keys | Right for production with real identities. Out of scope for a local, free project; the role design carries over unchanged. |

## Consequences

- **A customer request for internal data is refused by Weaviate** (`InsufficientPermissionsError`,
  HTTP 403 or gRPC "permission denied"), and `tests/security/` asserts the refusal, not an
  empty result.
- **v0-v4 stay insecure on purpose.** Their single collections hold both audiences and are
  read with the admin key, so earlier results remain reproducible as the "before" state.
- **Keys are secrets.** They live only in the git-ignored `.env` and are read as `SecretStr`.
  `setup_access.py` prints them masked. Rotating a key means re-running
  `setup_access.py` after deleting the line from `.env`.
- **Every later feature that reads documents must respect roles:**
  - the semantic cache (Session 15) must not serve an agent's answer to a customer
  - agent tools (Sessions 10-11) and the MCP server (Session 12) must run with the
    caller's role
  - See [docs/security.md](../security.md).
- **Agents pay for two searches** (two Weaviate queries plus an RRF merge, about 20 ms).
  Customers pay nothing extra.
- **This does not stop the model inventing a policy** when the playbook is out of reach.
  That is grounding (Session 8).

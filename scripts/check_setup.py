"""Check that the local development environment is ready.

Runs independent checks (Python version, Weaviate and its keys, FastEmbed, Groq) and
prints a PASS/FAIL line for each. Exits with code 1 if any check fails.

Since Session 7 Weaviate needs an API key. The checks confirm that anonymous access is
refused, that the admin key works, and that the customer and agent keys can read
exactly what their roles allow (see docs/security.md).

Usage:
    python scripts/check_setup.py
"""

from __future__ import annotations

import sys
from collections.abc import Callable

from rag_assistant.config import Settings, load_settings, load_stage
from rag_assistant.schemas import Role

REQUIRED_PYTHON = (3, 12)
ACCESS_STAGE = "v5"
TEST_SENTENCE = "My laptop charger stopped working after two weeks."
GROQ_PROMPT = "Say hello in five words"

CheckResult = tuple[bool, str]


def check_python() -> CheckResult:
    """Confirm the interpreter is the required Python minor version."""
    version = sys.version_info
    found = f"{version.major}.{version.minor}.{version.micro}"
    ok = (version.major, version.minor) == REQUIRED_PYTHON
    return ok, f"Python {found} (need {REQUIRED_PYTHON[0]}.{REQUIRED_PYTHON[1]})"


def check_weaviate(settings: Settings) -> CheckResult:
    """Connect with the admin key, ask whether Weaviate is ready and list collections."""
    from rag_assistant.ingestion.store import connect_admin

    cfg = settings.weaviate
    with connect_admin(settings) as client:
        if not client.is_ready():
            return False, f"Weaviate at {cfg.url} is not ready"
        version = client.get_meta().get("version", "unknown")
        names = sorted(client.collections.list_all())
        return True, f"Weaviate {version} at {cfg.url} (admin key); collections: {', '.join(names)}"


def check_anonymous_refused(settings: Settings) -> CheckResult:
    """Without a key, reading the schema must be refused (HTTP 401)."""
    import httpx

    status = httpx.get(
        f"{str(settings.weaviate.url).rstrip('/')}/v1/schema", timeout=10
    ).status_code
    return status == 401, f"anonymous GET /v1/schema -> HTTP {status} (expected 401)"


def check_role_key(settings: Settings, role: Role) -> CheckResult:
    """The role's key reads exactly the v5 collections its role allows."""
    from rag_assistant.ingestion.store import connect
    from rag_assistant.retrieval.access import key_for_role
    from rag_assistant.security.probes import describe_mismatches, read_data

    collections = load_stage(ACCESS_STAGE).audience_collections
    with connect(settings.weaviate, key_for_role(role, settings)) as client:
        observed = {audience: read_data(client, name) for audience, name in collections.items()}
    problems = describe_mismatches(role, observed)
    detail = ", ".join(f"{a} {o}" for a, o in observed.items())
    return not problems, f"{role} key: {detail}" + (f" ({'; '.join(problems)})" if problems else "")


def check_fastembed(settings: Settings) -> CheckResult:
    """Embed one sentence and compare the vector size with the config."""
    from fastembed import TextEmbedding

    cfg = settings.embedding
    model = TextEmbedding(model_name=cfg.model_name)
    vector = next(iter(model.embed([TEST_SENTENCE])))
    size = len(vector)
    ok = size == cfg.dimension
    return ok, f"{cfg.model_name} vector size {size} (expected {cfg.dimension})"


def check_groq(settings: Settings) -> CheckResult:
    """Send a tiny prompt to the small Groq model."""
    from langchain_groq import ChatGroq

    llm = ChatGroq(
        model=settings.llm.small_model,
        api_key=settings.groq_api_key,
        temperature=settings.llm.temperature,
        timeout=settings.llm.timeout_seconds,
        max_retries=1,
    )
    reply = str(llm.invoke(GROQ_PROMPT).content).strip()
    if not reply:
        return False, f"{settings.llm.small_model} returned an empty reply"
    return True, f'{settings.llm.small_model} replied: "{reply}"'


def run_check(name: str, check: Callable[[], CheckResult]) -> bool:
    """Run one check, print its result line and return whether it passed."""
    try:
        ok, detail = check()
    except Exception as exc:  # noqa: BLE001 - report every failure, never crash
        ok, detail = False, f"{type(exc).__name__}: {exc}"
    print(f"[{'PASS' if ok else 'FAIL'}] {name:<10} {detail}")
    return ok


def main() -> int:
    """Run all checks and return the process exit code."""
    print("Checking development setup...\n")
    results = [run_check("Python", check_python)]

    try:
        settings = load_settings()
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] {'Config':<10} {type(exc).__name__}: {exc}")
        print("\nFix the config (see .env.example and configs/base.yaml) and re-run.")
        return 1
    print(f"[PASS] {'Config':<10} loaded configs and .env")

    results += [
        run_check("Weaviate", lambda: check_weaviate(settings)),
        run_check("Anonymous", lambda: check_anonymous_refused(settings)),
        run_check("Customer", lambda: check_role_key(settings, "customer")),
        run_check("Agent", lambda: check_role_key(settings, "agent")),
        run_check("FastEmbed", lambda: check_fastembed(settings)),
        run_check("Groq", lambda: check_groq(settings)),
    ]

    passed = sum(results)
    print(f"\n{passed}/{len(results)} checks passed.")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())

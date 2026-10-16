"""Check that the local development environment is ready.

Runs four independent checks (Python version, Weaviate, FastEmbed, Groq) and
prints a PASS/FAIL line for each. Exits with code 1 if any check fails.

Usage:
    python scripts/check_setup.py
"""

from __future__ import annotations

import sys
from collections.abc import Callable

from rag_assistant.config import Settings, load_settings

REQUIRED_PYTHON = (3, 12)
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
    """Connect to Weaviate and ask whether it is ready."""
    import weaviate

    cfg = settings.weaviate
    client = weaviate.connect_to_custom(
        http_host=cfg.host,
        http_port=cfg.http_port,
        http_secure=cfg.secure,
        grpc_host=cfg.host,
        grpc_port=cfg.grpc_port,
        grpc_secure=cfg.secure,
    )
    try:
        if not client.is_ready():
            return False, f"Weaviate at {cfg.url} is not ready"
        version = client.get_meta().get("version", "unknown")
        return True, f"Weaviate {version} ready at {cfg.url} (gRPC {cfg.grpc_port})"
    finally:
        client.close()


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
        run_check("FastEmbed", lambda: check_fastembed(settings)),
        run_check("Groq", lambda: check_groq(settings)),
    ]

    passed = sum(results)
    print(f"\n{passed}/{len(results)} checks passed.")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())

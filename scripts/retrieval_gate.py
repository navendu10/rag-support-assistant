"""The CI retrieval gate: v7 recall@5 and retrieval leaks, with no LLM call (Session 13).

Usage:
    python scripts/retrieval_gate.py                    # check against the baseline
    python scripts/retrieval_gate.py --write-baseline   # record a new baseline (deliberately)

Needs Weaviate with v7's collections ingested and the role keys from setup_access.py.
Every golden question is searched with v7's stored rewrite, as its user. Exit 1 if recall@5
drops more than 2 questions below results/v7/retrieval_baseline.json, if any internal chunk
reaches a customer, if a v7 chunk can't find itself in the vector index, or if any LLM
request was made. See docs/evaluation.md ("The CI retrieval gate").
"""

from __future__ import annotations

import argparse
import json
import sys
import time

import weaviate
from weaviate.classes.init import Auth

from rag_assistant.config import get_settings, load_stage
from rag_assistant.eval.gate import (
    BASELINE_FILE,
    GateResult,
    baseline_record,
    leaks_for,
    load_baseline,
    problems,
)
from rag_assistant.eval.metrics import recall_hit
from rag_assistant.eval.runner import load_rows, output_dir
from rag_assistant.graph.handoff import MemoryTicketStore
from rag_assistant.llm import transport_stats
from rag_assistant.pipeline import Pipeline
from rag_assistant.schemas import load_golden, load_users


def parse_args() -> argparse.Namespace:
    """Parse command-line options."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write-baseline", action="store_true", help="record the baseline")
    return parser.parse_args()


def index_misses(collections: list[str]) -> list[str]:
    """Collections where some chunk isn't found by its own vector (admin key)."""
    settings = get_settings()
    key = settings.weaviate_admin_key
    client = weaviate.connect_to_local(
        host=settings.weaviate.host,
        port=settings.weaviate.http_port,
        grpc_port=settings.weaviate.grpc_port,
        auth_credentials=Auth.api_key(key.get_secret_value()) if key else None,
    )
    found = []
    try:
        for name in collections:
            col, total, missing = client.collections.get(name), 0, 0
            for obj in col.iterator(include_vector=True, return_properties=[]):
                vector = obj.vector.get("default") if isinstance(obj.vector, dict) else obj.vector
                total += 1
                hits = col.query.near_vector(near_vector=vector, limit=3).objects
                missing += not any(h.uuid == obj.uuid for h in hits)
            if missing:
                found.append(f"{name}: {missing}/{total} chunks not found by their own vector")
    finally:
        client.close()
    return found


def run_gate() -> GateResult:
    """Search every golden question with v7's stored rewrite, as its user."""
    settings = get_settings()
    stage = load_stage("v7")
    items = {i.id: i for i in load_golden(settings.paths.resolve(settings.paths.golden_file))}
    users = {u.user_id: u for u in load_users(settings.paths.resolve(settings.paths.users_file))}
    k = settings.eval.recall_k
    hits, questions, leaks, misses = 0, 0, [], []
    with Pipeline.from_stage(stage, settings, tickets=MemoryTicketStore()) as pipeline:
        for row in load_rows(output_dir(settings, stage, False)):
            rewrite, user = row.rewrite, users[row.user_id]
            if rewrite is None:
                continue
            found = pipeline.retrieve_ranked(
                rewrite.search_query,
                k,
                rerank_query=rewrite.standalone_question,
                extra_codes=rewrite.product_codes,
                regions=pipeline.regions_for(user),
                user=user,
            )
            leaks += leaks_for(row.id, user.user_id, user.role, [*found.ranked, *found.candidates])
            expected = items[row.id].expected_doc_ids
            if expected:
                questions += 1
                hit = recall_hit(expected, [c.doc_id for c in found.ranked[:k]])
                hits += bool(hit)
                if not hit:
                    misses.append(row.id)
    requests = sum(transport_stats(m).requests for m in {settings.llm.small_model,
                   settings.llm.large_model, settings.llm.judge_model,
                   settings.llm.generation_model})  # fmt: skip
    collections = sorted(stage.audience_collections.values())
    return GateResult(hits, questions, sorted(set(leaks)), index_misses(collections), requests,
                      misses)  # fmt: skip


def main() -> int:
    """Run the gate, print the numbers, and fail if any check fails."""
    args = parse_args()
    start = time.perf_counter()
    result = run_gate()
    print(f"recall@5 {result.recall_hits}/{result.questions}; misses: {', '.join(result.misses)}")
    print(f"retrieval leaks: {len(result.leaks)}; index problems: {len(result.index_misses)}; "
          f"LLM requests: {result.llm_requests}; {time.perf_counter() - start:.0f} s")  # fmt: skip
    path = get_settings().paths.resolve(BASELINE_FILE)
    if args.write_baseline:
        record = baseline_record(result, "v7, stored rewrites, tie-break on (Session 13)")
        path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8", newline="\n")
        print(f"Wrote {path}")
        return 0
    found = problems(result, load_baseline(path))
    for problem in found:
        print(f"FAIL: {problem}")
    print("Retrieval gate passed." if not found else "Retrieval gate FAILED.")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())

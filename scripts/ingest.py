"""Ingest the generated knowledge base into the stage's Weaviate collection.

Usage:
    python scripts/ingest.py --stage v0

A stage with ``index:`` set (e.g. v3 searches Chunks_v2) has nothing to ingest: the
script says which stage owns the collection and exits without changing it.
"""

from __future__ import annotations

import argparse
import sys
import time

from rag_assistant.config import get_settings, load_stage
from rag_assistant.ingestion.pipeline import ingest


def parse_args() -> argparse.Namespace:
    """Parse command-line options."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stage", required=True, help="stage name, e.g. v0")
    return parser.parse_args()


def main() -> int:
    """Run ingestion and print what was stored."""
    args = parse_args()
    stage = load_stage(args.stage)
    if stage.reuses_index:
        print(
            f"Stage {stage.name} reuses {stage.collection_name} (built by stage "
            f"{stage.index_owner}); nothing to ingest. To rebuild it, run: "
            f"python scripts/ingest.py --stage {stage.index_owner}"
        )
        return 0
    start = time.perf_counter()
    stats = ingest(stage, get_settings())
    print(
        f"Ingested {stats.documents} documents as {stats.chunks} chunks into "
        f"{stats.collection} in {time.perf_counter() - start:.1f}s"
    )
    for audience, count in sorted(stats.chunks_by_audience.items()):
        print(f"  {audience:<9} {count} chunks")
    return 0


if __name__ == "__main__":
    sys.exit(main())

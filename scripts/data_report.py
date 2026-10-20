"""Print a summary of the generated documents and the golden test set.

Usage:
    python scripts/data_report.py [--generated DIR] [--golden FILE]
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

from rag_assistant.schemas import load_golden, load_manifest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GENERATED = PROJECT_ROOT / "data" / "generated"
DEFAULT_GOLDEN = PROJECT_ROOT / "data" / "golden" / "questions.jsonl"


def parse_args() -> argparse.Namespace:
    """Parse command-line options."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--generated", type=Path, default=DEFAULT_GENERATED)
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    return parser.parse_args()


def print_counts(title: str, counts: Counter[str]) -> None:
    """Print one small table of counts, largest first then alphabetical."""
    print(f"\n{title}")
    for key, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"  {key:<14} {count:>3}")
    print(f"  {'total':<14} {sum(counts.values()):>3}")


def main() -> int:
    """Print the report, or explain how to generate the data first."""
    args = parse_args()
    manifest = args.generated / "manifest.json"
    if not manifest.is_file():
        print(f"No manifest at {manifest}. Run: python scripts/generate_data.py")
        return 1

    docs = load_manifest(manifest)
    golden = load_golden(args.golden)
    print(f"Knowledge base: {len(docs)} documents ({manifest})")
    print_counts("Documents per type", Counter(d.doc_type for d in docs))
    print_counts("Documents per region", Counter(d.region for d in docs))
    print_counts("Documents per audience", Counter(d.audience for d in docs))
    print(f"\nGolden set: {len(golden)} questions ({args.golden})")
    print_counts("Questions per failure tag", Counter(g.failure_tag for g in golden))
    print(f"\nQuestions expecting a handoff: {sum(g.expect_handoff for g in golden)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

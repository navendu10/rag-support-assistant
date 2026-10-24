"""Print a before/after Markdown table for every evaluated stage.

Usage:
    python scripts/compare_stages.py            # headline metrics per stage
    python scripts/compare_stages.py --by-tag   # also correctness/recall per failure tag
"""

from __future__ import annotations

import argparse
import sys

from rag_assistant.config import PathSettings
from rag_assistant.eval.compare import comparison_table, load_summaries, tag_comparison_table


def parse_args() -> argparse.Namespace:
    """Parse command-line options."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--by-tag", action="store_true", help="add a per-failure-tag table")
    return parser.parse_args()


def main() -> int:
    """Print the comparison table(s), or explain how to create results."""
    args = parse_args()
    paths = PathSettings()
    summaries = load_summaries(paths.resolve(paths.results_dir))
    if not summaries:
        print("No results yet. Run: python scripts/evaluate.py --stage v0")
        return 1
    print(comparison_table(summaries))
    if args.by_tag:
        print()
        print(tag_comparison_table(summaries))
    return 0


if __name__ == "__main__":
    sys.exit(main())

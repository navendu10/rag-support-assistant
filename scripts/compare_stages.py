"""Print a before/after Markdown table for every evaluated stage.

Usage:
    python scripts/compare_stages.py
"""

from __future__ import annotations

import sys

from rag_assistant.config import get_settings
from rag_assistant.eval.compare import comparison_table, load_summaries


def main() -> int:
    """Print the comparison table, or explain how to create results."""
    settings = get_settings()
    summaries = load_summaries(settings.paths.resolve(settings.paths.results_dir))
    if not summaries:
        print("No results yet. Run: python scripts/evaluate.py --stage v0")
        return 1
    print(comparison_table(summaries))
    return 0


if __name__ == "__main__":
    sys.exit(main())

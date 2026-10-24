"""Print a document's chunks for a stage, exactly as ingestion would create them.

Builds the chunks offline (no Weaviate needed) and prints each chunk's context
header, every metadata field it has, its length and its text.

Usage:
    python scripts/show_chunks.py --stage v0 --doc returns-uk-2026
    python scripts/show_chunks.py --stage v0 --doc returns-uk-2026 --grep "Opened"
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from rag_assistant.config import PathSettings, load_stage
from rag_assistant.ingestion.chunk_listing import format_listing
from rag_assistant.ingestion.pipeline import chunk_documents


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse command-line options."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stage", required=True, help="stage name, e.g. v0")
    parser.add_argument("--doc", required=True, help="doc_id from data/generated/manifest.json")
    parser.add_argument("--grep", help="only chunks containing this text (case-insensitive)")
    parser.add_argument(
        "--generated",
        type=Path,
        help="folder with manifest.json (default: data/generated)",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Print the listing, or a clear error for an unknown stage or document."""
    args = parse_args(argv)
    paths = PathSettings()
    generated = args.generated or paths.resolve(paths.generated_dir)
    try:
        stage = load_stage(args.stage)
        chunks = chunk_documents(stage, generated, doc_ids={args.doc})
    except (FileNotFoundError, KeyError, NotImplementedError) as exc:
        # KeyError's str() adds quotes, so print the message itself.
        print(f"error: {exc.args[0] if exc.args else exc}", file=sys.stderr)
        return 1
    sys.stdout.reconfigure(encoding="utf-8")
    print(format_listing(chunks, stage.name, args.doc, args.grep), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())

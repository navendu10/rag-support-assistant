"""Generate the fictional retailer's knowledge base into data/generated/.

Usage:
    python scripts/generate_data.py [--out DIR]
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "data"))

from generators.build import build_all  # noqa: E402

DEFAULT_OUT = PROJECT_ROOT / "data" / "generated"


def parse_args() -> argparse.Namespace:
    """Parse command-line options."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output directory")
    return parser.parse_args()


def main() -> int:
    """Generate every document and print a one-line summary per type."""
    args = parse_args()
    docs = build_all(args.out)
    counts = Counter(d.doc_type for d in docs)
    print(f"Generated {len(docs)} documents in {args.out}")
    for doc_type, count in sorted(counts.items()):
        print(f"  {doc_type:<9} {count}")
    print(f"Manifest: {args.out / 'manifest.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

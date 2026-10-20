"""Run every document family and write the manifest."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

from generators import faqs, manuals, marketplace, playbooks, policies, returns
from generators.common import write_manifest
from rag_assistant.schemas import DocMetadata

FAMILIES: tuple[Callable[[Path], list[DocMetadata]], ...] = (
    manuals.build,
    returns.build,
    policies.build,
    faqs.build,
    playbooks.build,
    marketplace.build,
)

# Only these paths are ever deleted when cleaning the output directory.
OUTPUT_SUBDIRS = ("manuals", "policies", "faqs", "playbooks", "marketplace")
MANIFEST_NAME = "manifest.json"


def clean_output(out_dir: Path) -> None:
    """Remove previously generated files, leaving anything else (e.g. .gitkeep) alone."""
    for name in OUTPUT_SUBDIRS:
        shutil.rmtree(out_dir / name, ignore_errors=True)
    (out_dir / MANIFEST_NAME).unlink(missing_ok=True)


def build_all(out_dir: Path) -> list[DocMetadata]:
    """Regenerate the whole knowledge base in ``out_dir``.

    Returns:
        Metadata for every document, sorted by ``doc_id``.

    Raises:
        ValueError: if two documents share a ``doc_id``.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    clean_output(out_dir)
    docs = [doc for family in FAMILIES for doc in family(out_dir)]
    ids = [d.doc_id for d in docs]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise ValueError(f"Duplicate doc_ids: {duplicates}")
    write_manifest(out_dir, docs)
    return sorted(docs, key=lambda d: d.doc_id)

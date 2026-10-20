"""Shared fixtures: generate the knowledge base once per test session."""

from __future__ import annotations

from pathlib import Path

import pytest
from pypdf import PdfReader

from generators.build import build_all
from rag_assistant.schemas import DocMetadata, load_manifest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GOLDEN_PATH = PROJECT_ROOT / "data" / "golden" / "questions.jsonl"
USERS_PATH = PROJECT_ROOT / "data" / "users.json"


def normalise(text: str) -> str:
    """Drop Markdown table pipes and collapse whitespace, so PDF and Markdown compare alike."""
    return " ".join(text.replace("|", " ").split())


def extract_text(path: Path) -> str:
    """Return a document's full text (PDF via pypdf, others read as-is), normalised."""
    if path.suffix == ".pdf":
        raw = "\n".join(page.extract_text() for page in PdfReader(path).pages)
    else:
        raw = path.read_text(encoding="utf-8")
    return normalise(raw)


@pytest.fixture(scope="session")
def generated_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A freshly generated knowledge base in a temporary directory."""
    out = tmp_path_factory.mktemp("generated")
    build_all(out)
    return out


@pytest.fixture(scope="session")
def docs(generated_dir: Path) -> dict[str, DocMetadata]:
    """Manifest entries keyed by doc_id."""
    return {d.doc_id: d for d in load_manifest(generated_dir / "manifest.json")}


@pytest.fixture(scope="session")
def texts(generated_dir: Path, docs: dict[str, DocMetadata]) -> dict[str, str]:
    """Normalised text of every document keyed by doc_id."""
    return {doc_id: extract_text(generated_dir / d.source_path) for doc_id, d in docs.items()}

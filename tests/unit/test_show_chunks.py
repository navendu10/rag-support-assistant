"""Tests for scripts/show_chunks.py and the chunk listing helpers (no network)."""

from __future__ import annotations

import importlib.util
import re
from dataclasses import dataclass, fields
from pathlib import Path
from types import ModuleType

import pytest

from rag_assistant.config import load_stage
from rag_assistant.ingestion.chunk_listing import (
    context_header,
    filter_chunks,
    format_chunk,
    format_listing,
)
from rag_assistant.ingestion.chunker import Chunk
from rag_assistant.ingestion.pipeline import chunk_documents

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "show_chunks.py"


def _load_script() -> ModuleType:
    """Import scripts/show_chunks.py as a module (scripts/ is not a package)."""
    spec = importlib.util.spec_from_file_location("show_chunks", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@dataclass(frozen=True)
class RichChunk:
    """Stands in for a later stage's chunk, which has more metadata than v0's."""

    text: str
    doc_id: str
    chunk_index: int
    section: str
    page: int | None
    product_code: str | None


def test_v0_listing_prints_every_v0_field(generated_dir: Path, capsys) -> None:
    """The script runs for v0 and prints each field the v0 Chunk has, plus the length."""
    code = _load_script().main(
        ["--stage", "v0", "--doc", "returns-uk-2026", "--generated", str(generated_dir)]
    )
    out = capsys.readouterr().out
    assert code == 0
    chunks = chunk_documents(load_stage("v0"), generated_dir, doc_ids={"returns-uk-2026"})
    assert out.startswith(f"returns-uk-2026 | stage v0 | {len(chunks)} chunks\n")
    for field in fields(Chunk):
        if field.name != "text":
            assert f"\n{field.name} " in out, field.name
    assert re.search(r"\nheader +: \(none\)\n", out)  # v0 chunks have no context header
    for chunk in chunks:
        assert f"chunk {chunk.chunk_index} | {len(chunk.text)} chars" in out
        assert chunk.text in out


def test_grep_filters_case_insensitively(generated_dir: Path, capsys) -> None:
    """--grep keeps only matching chunks and says how many matched."""
    code = _load_script().main(
        [
            "--stage", "v0",
            "--doc", "returns-uk-2026",
            "--grep", "| OPENED | 15 DAYS",
            "--generated", str(generated_dir),
        ]
    )  # fmt: skip
    out = capsys.readouterr().out
    assert code == 0
    assert "1 matching '| OPENED | 15 DAYS'" in out
    assert out.count("\nchunk ") == 1


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--stage", "v0", "--doc", "no-such-doc"], "Unknown doc_id(s): no-such-doc"),
        (["--stage", "nope", "--doc", "returns-uk-2026"], "Stage config not found"),
    ],
)
def test_clear_errors(generated_dir: Path, capsys, args: list[str], message: str) -> None:
    """Unknown documents and stages exit with code 1 and a readable message."""
    code = _load_script().main([*args, "--generated", str(generated_dir)])
    err = capsys.readouterr().err
    assert code == 1
    assert err.startswith("error: ")
    assert message in err
    assert "'Unknown" not in err  # no stray KeyError quotes


def test_format_chunk_prints_whatever_fields_exist() -> None:
    """Extra metadata from later stages is printed without code changes."""
    chunk = RichChunk(
        text="[Returns Policy > Return windows]\n| a | b |",
        doc_id="returns-uk-2026-print",
        chunk_index=2,
        section="Return windows",
        page=1,
        product_code=None,
    )
    out = format_chunk(chunk)
    assert out.splitlines()[1] == f"chunk 2 | {len(chunk.text)} chars"
    assert "header       : [Returns Policy > Return windows]" in out
    assert "section      : Return windows" in out
    assert "page         : 1" in out
    assert "product_code : None" in out
    assert "\ntext :" not in out  # the text is printed once, not as a metadata field


def test_context_header_detection() -> None:
    """Only a bracketed first line counts as a header."""
    assert context_header("[Title > Section]\nbody") == "[Title > Section]"
    assert context_header("| Product | Window |\n|---|---|") is None
    assert context_header("Body [not a header]") is None


def test_filter_and_listing_without_matches() -> None:
    """No matches gives an explicit message rather than silent output."""
    chunks = [RichChunk("alpha", "d", 0, "", None, None)]
    assert filter_chunks(chunks, None) == chunks
    assert filter_chunks(chunks, "ALPHA") == chunks
    listing = format_listing(chunks, "v0", "d", grep="zeta")
    assert "0 matching 'zeta'" in listing
    assert "No chunk contains 'zeta'." in listing


def test_format_chunk_rejects_non_dataclasses() -> None:
    """The listing relies on dataclass fields."""
    with pytest.raises(TypeError):
        format_chunk("not a chunk")


def test_doc_subset_matches_full_ingestion(generated_dir: Path) -> None:
    """Chunks for one document are exactly the ones full ingestion would create."""
    stage = load_stage("v0")
    every = chunk_documents(stage, generated_dir)
    one = chunk_documents(stage, generated_dir, doc_ids={"manual-xr-2200"})
    assert one == [c for c in every if c.doc_id == "manual-xr-2200"]


def test_v1_listing_shows_structure_metadata(generated_dir: Path, capsys) -> None:
    """For v1 the same script shows the context header plus section and page."""
    code = _load_script().main(
        ["--stage", "v1", "--doc", "returns-uk-2026-print", "--generated", str(generated_dir)]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "header         : [VoltCart Returns Policy (UK) > " in out
    assert "section        : " in out and "page           : 1" in out

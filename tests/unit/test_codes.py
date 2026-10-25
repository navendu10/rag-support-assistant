"""Tests for product and error code detection (hybrid search's dynamic alpha)."""

from __future__ import annotations

from pathlib import Path

import pytest

from rag_assistant.retrieval.codes import CodeDetector, code_pattern
from rag_assistant.schemas import DocMetadata

MANIFEST_CODES = (
    "AB-PRO",
    "NB-14",
    "PB-20K",
    "QC-USBC",
    "SW-FIT2",
    "VM-55Q",
    "WM-700",
    "XR-2000",
    "XR-2200",
)


@pytest.fixture(scope="module")
def detector(generated_dir: Path) -> CodeDetector:
    """A detector built from the freshly generated manifest."""
    return CodeDetector.from_manifest(generated_dir / "manifest.json")


def test_detector_reads_every_manifest_code(
    detector: CodeDetector, docs: dict[str, DocMetadata]
) -> None:
    """Every product code in the manifest is known, and nothing else."""
    assert detector.product_codes == MANIFEST_CODES
    assert set(detector.product_codes) == {d.product_code for d in docs.values() if d.product_code}


@pytest.mark.parametrize("code", MANIFEST_CODES)
def test_each_code_matches_with_and_without_hyphen(detector: CodeDetector, code: str) -> None:
    """Codes match as written, without the hyphen and in lower case."""
    for spelling in (code, code.replace("-", ""), code.lower(), code.replace("-", "").lower()):
        assert detector.find(f"Question about the {spelling}, please?") == [code], spelling


@pytest.mark.parametrize(
    ("text", "codes"),
    [
        ("What does error E-47 mean?", ["E-47"]),
        ("it shows e74", ["E-74"]),
        ("Codes E-10 and E-90 keep coming back", ["E-10", "E-90"]),
    ],
)
def test_error_codes(detector: CodeDetector, text: str, codes: list[str]) -> None:
    """E-NN error codes are found with or without the hyphen, in canonical form."""
    assert detector.find(text) == codes


@pytest.mark.parametrize(
    "text",
    [
        "How do I return something?",
        "Does it support Wi-Fi 6?",
        "I bought it in 2026 for 300 GBP",
        "Is the AeroBuds Pro waterproof?",  # a product name, not a code
        "My email address changed",
        "XR-22000 is not a model",
        "the box said WM-7000",
    ],
)
def test_ordinary_text_has_no_codes(detector: CodeDetector, text: str) -> None:
    """Ordinary words, Wi-Fi standards, years, names and longer numbers are not codes."""
    assert detector.find(text) == []


def test_similar_codes_stay_distinct(detector: CodeDetector) -> None:
    """XR-2000 and XR-2200 are different codes; order of appearance is kept, repeats dropped."""
    assert detector.find("xr2200 or XR-2000? I mean the XR-2200") == ["XR-2200", "XR-2000"]


def test_canonicalise_rewrites_codes_only(detector: CodeDetector) -> None:
    """Keyword text gets canonical codes; the rest of the question is unchanged."""
    assert detector.canonicalise("Reset the xr2200 after e47?") == "Reset the XR-2200 after E-47?"
    assert detector.canonicalise("No codes here.") == "No codes here."


def test_from_codes_deduplicates_and_uppercases() -> None:
    """Codes from any source are normalised and sorted."""
    assert CodeDetector.from_codes(["xr-2200", "XR-2200", "ab-pro"]).product_codes == (
        "AB-PRO",
        "XR-2200",
    )


def test_code_pattern_makes_each_hyphen_optional() -> None:
    """A multi-part code matches with any of its hyphens left out."""
    pattern = code_pattern("A-B-1")
    assert all(pattern.fullmatch(s) for s in ("A-B-1", "AB1", "a-b1", "ab-1"))
    assert not pattern.fullmatch("A B 1")

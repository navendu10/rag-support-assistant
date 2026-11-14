"""Layer 4 of the injection defence (Session 9): nothing worth stealing in the data.

An injection that makes the assistant reveal its sources can only leak what is there. So
a freshly generated knowledge base must hold no discount code, API key, password or
token. The canary ``INTERNAL-CANARY-7731`` is there on purpose (it detects leaks of the
internal playbooks) and is the only allowed code-like string.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

from generators.facts import CANARY
from tests.unit.conftest import extract_text

SECRET_PATTERNS = {
    "Groq API key": re.compile(r"\bgsk_[A-Za-z0-9]{20,}"),
    "OpenAI-style key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "bearer token": re.compile(r"\bBearer\s+[A-Za-z0-9._-]{20,}", re.IGNORECASE),
    "password or secret with a value": re.compile(
        r"\b(password|passwd|pwd|secret|api[_ -]?key|token)\s*[:=]\s*\S{4,}", re.IGNORECASE
    ),
    "long hex secret": re.compile(r"\b[0-9a-f]{32,}\b", re.IGNORECASE),
    "long base64 secret": re.compile(r"\b[A-Za-z0-9+/]{40,}={0,2}"),
    "discount code": re.compile(
        r"\b(code|coupon|promo|voucher)\s*[:=]?\s*[A-Z][A-Z0-9]{3,}\d+\b|"
        r"\b(SAVE|DISCOUNT|PROMO|VOLT|WELCOME)\d{1,3}\b",
    ),
}


def _texts(root: Path) -> dict[str, str]:
    """Every generated document and the manifest as text, keyed by path under ``root``
    (PDFs through the same text extraction the data tests use)."""
    return {
        p.relative_to(root).as_posix(): extract_text(p) if p.suffix == ".pdf"
        else p.read_text(encoding="utf-8")
        for p in root.rglob("*")
        if p.is_file() and p.suffix in {".md", ".html", ".json", ".pdf"}
    }  # fmt: skip


def find_secrets(text: str) -> list[str]:
    """``"<kind>: <match>"`` for every secret-looking string, the canary excepted."""
    text = text.replace(CANARY, "")
    return [
        f"{kind}: {m.group(0)}" for kind, pattern in SECRET_PATTERNS.items()
        for m in pattern.finditer(text)
    ]  # fmt: skip


def test_generated_data_holds_nothing_worth_stealing(generated_dir: Path) -> None:
    """No document, PDF or the manifest contains a key, password, token or discount code."""
    found = {
        path: hits for path, text in _texts(generated_dir).items()
        if (hits := find_secrets(text))
    }  # fmt: skip
    assert found == {}


# Planted test secrets are built at run time, so no secret-looking literal sits in the
# source for secret scanners (GitGuardian) to flag. None of them is a real credential.
_FAKE_HEX = hashlib.sha256(b"not a real secret").hexdigest()[:32]
PLANTED = [
    "Use code SAVE20 at checkout.",
    "Staff discount code: VOLTSTAFF25",
    "GROQ_API_KEY=" + "gsk" + "_" + "x1" * 16,
    "admin " + "pass" + "word: " + "x" * 8,
    "token = " + _FAKE_HEX,
]


@pytest.mark.parametrize("planted", PLANTED)
def test_the_scan_catches_planted_secrets(planted: str) -> None:
    """The scan itself works: each kind of planted secret is found."""
    assert find_secrets(f"Some policy text. {planted} More text.")


def test_the_canary_is_allowed() -> None:
    """The deliberate leak canary is not a secret."""
    assert find_secrets(f"(Internal reference {CANARY})") == []

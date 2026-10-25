"""Find product and error codes in a question (used by hybrid search, Session 4).

Codes are where embeddings are weakest: "XR-2200" and "XR-2000", or "E-47" and
"E-74", embed almost identically. Detecting them lets the retriever lean on keyword
search for those questions.

Product codes come from the manifest rather than a generic pattern, because
real codes have no single shape (VM-55Q, PB-20K, AB-PRO, QC-USBC). Each code
matches case-insensitively with or without its hyphen ("xr2200"). Error codes
follow one pattern, E-47 or E47.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from rag_assistant.schemas import load_manifest

ERROR_CODE = re.compile(r"\bE-?(\d{2,3})\b", re.IGNORECASE)


def code_pattern(code: str) -> re.Pattern[str]:
    """Case-insensitive pattern for ``code`` where each hyphen is optional."""
    body = "-?".join(re.escape(part) for part in code.split("-"))
    return re.compile(rf"\b{body}\b", re.IGNORECASE)


@dataclass(frozen=True)
class CodeDetector:
    """Detects known product codes and E-NN error codes, returning canonical spellings."""

    product_codes: tuple[str, ...]
    _patterns: tuple[tuple[str, re.Pattern[str]], ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        """Compile one pattern per product code."""
        patterns = tuple((code, code_pattern(code)) for code in self.product_codes)
        object.__setattr__(self, "_patterns", patterns)

    @classmethod
    def from_codes(cls, codes: Iterable[str]) -> CodeDetector:
        """Build from any iterable of product codes (deduplicated and sorted)."""
        return cls(tuple(sorted({c.upper() for c in codes})))

    @classmethod
    def from_manifest(cls, manifest_path: Path) -> CodeDetector:
        """Build from every non-null ``product_code`` in ``manifest.json``."""
        return cls.from_codes(
            d.product_code for d in load_manifest(manifest_path) if d.product_code
        )

    def _matches(self, text: str) -> list[tuple[int, int, str]]:
        """(start, end, canonical code) for every code in ``text``, in order."""
        found = [
            (m.start(), m.end(), code)
            for code, pattern in self._patterns
            for m in pattern.finditer(text)
        ]
        found += [(m.start(), m.end(), f"E-{m.group(1)}") for m in ERROR_CODE.finditer(text)]
        return sorted(found)

    def find(self, text: str) -> list[str]:
        """Canonical codes in ``text`` in order of appearance, without repeats."""
        return list(dict.fromkeys(code for _, _, code in self._matches(text)))

    def canonicalise(self, text: str) -> str:
        """``text`` with each code rewritten in its canonical form ("xr2200" -> "XR-2200").

        Keyword search tokenises "XR-2200" as "xr" + "2200", so an unhyphenated
        "xr2200" would otherwise be a single token that matches nothing.
        """
        parts, last = [], 0
        for start, end, code in self._matches(text):
            if start < last:
                continue
            parts += [text[last:start], code]
            last = end
        return "".join([*parts, text[last:]])

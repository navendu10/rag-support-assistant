"""Layer 1 of the injection defence (Session 9): clean documents at ingestion.

Every document gets a **trust level** from its ``doc_type``: VoltCart's own documents
(policies, manuals, FAQs, playbooks) are ``store``; marketplace listings, and any type
this code doesn't know, are ``third_party``. The level is stored on every chunk, so the
prompt (layer 2) and the output guard (layer 3) can tell VoltCart's word from a seller's.

For third-party HTML, hidden content is removed **before** chunking: elements hidden by
style or attributes, HTML comments, and invisible characters (zero-width, soft hyphens,
bidirectional controls, Unicode tag characters). Invisible characters go first, so an
instruction split by zero-width spaces can't slip past the phrase check.

Every chunk is then checked for instruction-like phrases (``injection.patterns`` in
``configs/base.yaml``). A flagged **third-party** chunk is dropped and logged; a flagged
**store** chunk is only logged, because VoltCart's own text may legitimately say "tell
the customer" (the fraud playbook does).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Literal

from bs4 import BeautifulSoup, Comment, Tag

from rag_assistant.ingestion.chunker import Chunk

logger = logging.getLogger(__name__)

Trust = Literal["store", "third_party"]
STORE_DOC_TYPES = frozenset({"policy", "manual", "faq", "playbook"})

# Zero-width and joiner characters, soft hyphen, word joiner and BOM, bidirectional
# controls, and Unicode tag characters (U+E0000-E007F, used to smuggle hidden text).
_INVISIBLE = re.compile("[­​-‏  ‪-‮⁠-⁤⁦-⁩﻿\U000e0000-\U000e007f]")
_HIDDEN_STYLE = re.compile(
    r"display\s*:\s*none|visibility\s*:\s*hidden|font-size\s*:\s*0(?![.\d]*[1-9])"
    r"|opacity\s*:\s*0(?![.\d]*[1-9])",
    re.IGNORECASE,
)


def trust_for(doc_type: str | None) -> Trust:
    """``store`` for VoltCart's own document types; ``third_party`` for anything else."""
    return "store" if doc_type in STORE_DOC_TYPES else "third_party"


def strip_invisible(text: str) -> str:
    """Remove zero-width, bidirectional-control, soft-hyphen and tag characters."""
    return _INVISIBLE.sub("", text)


def _is_hidden(tag: Tag) -> bool:
    """Hidden by an inline style, the ``hidden`` attribute or ``aria-hidden="true"``."""
    if tag.has_attr("hidden") or str(tag.get("aria-hidden", "")).lower() == "true":
        return True
    return bool(_HIDDEN_STYLE.search(str(tag.get("style", ""))))


def sanitize_html(html: str) -> str:
    """Third-party HTML with invisible characters, comments and hidden elements removed."""
    soup = BeautifulSoup(strip_invisible(html), "html.parser")
    for comment in soup.find_all(string=lambda s: isinstance(s, Comment)):
        comment.extract()
    for tag in [t for t in soup.find_all(True) if _is_hidden(t)]:
        tag.decompose()
    return str(soup)


@dataclass(frozen=True)
class Flag:
    """An instruction-like phrase found in a chunk."""

    doc_id: str
    chunk_index: int
    trust: Trust
    phrase: str
    dropped: bool


@dataclass(frozen=True)
class Screening:
    """The chunks to keep, and every flag raised (dropped or only logged)."""

    kept: list[Chunk]
    flags: list[Flag]

    @property
    def dropped(self) -> list[Flag]:
        """Flags whose chunk was dropped (third-party only)."""
        return [f for f in self.flags if f.dropped]


def compile_patterns(patterns: Sequence[str]) -> list[re.Pattern[str]]:
    """The configured phrase patterns, case-insensitive."""
    return [re.compile(p, re.IGNORECASE) for p in patterns]


def find_instruction(text: str, patterns: Sequence[re.Pattern[str]]) -> str | None:
    """The first instruction-like phrase in ``text`` (invisible characters removed first)."""
    clean = strip_invisible(text)
    for pattern in patterns:
        match = pattern.search(clean)
        if match:
            return match.group(0).strip()
    return None


def with_trust(chunks: Sequence[Chunk]) -> list[Chunk]:
    """The chunks with ``trust`` set from their ``doc_type``."""
    return [replace(c, trust=trust_for(c.doc_type)) for c in chunks]


def screen_chunks(chunks: Sequence[Chunk], patterns: Sequence[re.Pattern[str]]) -> Screening:
    """Flag instruction-like chunks: drop third-party ones, keep (and log) store ones."""
    kept, flags = [], []
    for chunk in chunks:
        phrase = find_instruction(chunk.text, patterns)
        if phrase is None:
            kept.append(chunk)
            continue
        dropped = chunk.trust != "store"
        flags.append(Flag(chunk.doc_id, chunk.chunk_index, chunk.trust, phrase, dropped))
        if dropped:
            logger.warning(
                "INJECTION: dropped third-party chunk %s#%d (%r)",
                chunk.doc_id,
                chunk.chunk_index,
                phrase,
            )
        else:
            logger.info(
                "INJECTION: store chunk %s#%d flagged, kept (%r)",
                chunk.doc_id,
                chunk.chunk_index,
                phrase,
            )
            kept.append(chunk)
    return Screening(kept, flags)

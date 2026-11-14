"""Layer 3 of the injection defence (Session 9): check the answer's facts before sending it.

An injected instruction usually aims at a **fact**: a longer warranty, a discount, a
credit. So the guard checks the facts that matter, sentence by sentence:

- **Numbers with a unit** (days, weeks, months, years, hours, %, GBP, USD, Rs) must appear
  in at least one cited **store** source ("fifteen days" and "15-day" count as 15 days;
  "£20" as GBP 20). A number found only in a third-party source passes only if the
  **same sentence** attributes it to the seller (``attribution_terms``) **and** cites that
  third-party source. But a sentence about VoltCart's own policy (returns, refunds,
  credits, discounts, VoltCart's warranty: ``voltcart_policy_terms``) always needs a store
  source, attribution or not. A listing can't make a VoltCart promise.
- **Claim phrases** ("lifetime warranty", "guaranteed forever") fail unless a cited store
  source contains them. A phrase that the sentence negates before it ("does not have a
  lifetime warranty") is a denial and passes; numbers get no such exemption.

The guard is deterministic and needs no LLM. The corrective flow regenerates once when it
fails and hands off if the second answer fails too (``graph/corrective.py``).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from rag_assistant.retrieval.vector import RetrievedChunk

logger = logging.getLogger(__name__)

# U+2010-2015 and U+2212 dashes; U+00A0, U+2007, U+2009, U+202F spaces.
_TYPOGRAPHY = str.maketrans(
    {**dict.fromkeys("\u2010\u2011\u2012\u2013\u2014\u2015\u2212", "-"),
     **dict.fromkeys("\u00a0\u2007\u2009\u202f", " ")}
)  # fmt: skip
_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100,
}  # fmt: skip
_NUM = r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|" + "|".join(_WORDS) + r")"
_TIME_UNITS = {
    "day": "day", "days": "day", "week": "week", "weeks": "week", "month": "month",
    "months": "month", "year": "year", "years": "year", "hour": "hour", "hours": "hour",
}  # fmt: skip
_CURRENCIES = {
    "£": "GBP", "gbp": "GBP", "$": "USD", "usd": "USD", "₹": "RS", "rs": "RS", "rs.": "RS",
    "inr": "RS",
}  # fmt: skip
_CURRENCY = r"(£|\$|₹|gbp|usd|inr|rs\.?)"
_TIME = re.compile(
    rf"\b{_NUM}[\s-]+(?:working[\s-]+|business[\s-]+|calendar[\s-]+)?"
    r"(days?|weeks?|months?|years?|hours?)\b",
    re.IGNORECASE,
)
_PERCENT = re.compile(rf"\b{_NUM}\s*(?:%|percent\b|per\s+cent\b)", re.IGNORECASE)
_MONEY_BEFORE = re.compile(rf"(?<![a-z]){_CURRENCY}\s*{_NUM}", re.IGNORECASE)
_MONEY_AFTER = re.compile(rf"\b{_NUM}\s*(gbp|usd|inr)\b", re.IGNORECASE)
_CLAUSE_BREAK = re.compile(r"[,;:]|but|however|though|although")
_NEGATION_WINDOW = 6  # words before the phrase that may hold its negation
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n+")
_MARKER = re.compile(r"\[(\d+)\]")
_NEGATION = re.compile(
    r"\b(not|no|never|without|isn't|doesn't|don't|aren't|won't|cannot|can't|nor)\b|"
    r"\brather than\b|\binstead of\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, order=True)
class Quantity:
    """A normalised number with a unit, e.g. ``Quantity("15", "day")``."""

    value: str
    unit: str

    @property
    def label(self) -> str:
        """How a person writes it: ``15 days``, ``6 months``, ``GBP 20``, ``10%``."""
        if self.unit in {"GBP", "USD", "RS"}:
            return f"{'Rs' if self.unit == 'RS' else self.unit} {self.value}"
        if self.unit == "percent":
            return f"{self.value}%"
        return f"{self.value} {self.unit}{'' if self.value == '1' else 's'}"

    def __str__(self) -> str:
        """``15 day``, ``GBP 20``, ``10 percent``."""
        return (
            f"{self.unit} {self.value}"
            if self.unit in {"GBP", "USD", "RS"}
            else (f"{self.value} {self.unit}")
        )


def _number(text: str) -> str:
    """``"2,000"`` -> ``"2000"``, ``"fifteen"`` -> ``"15"``, ``"9.990"`` -> ``"9.99"``."""
    word = _WORDS.get(text.lower())
    value = Decimal(word) if word is not None else Decimal(text.replace(",", ""))
    return format(value.normalize(), "f")


def fold_typography(text: str) -> str:
    """Unicode dashes become ``-`` and no-break or thin spaces a plain space.

    LLMs write "6\u2011month" (a non-breaking hyphen) and "15\u202fdays" as often as the
    plain forms; without this, the guard would never see those figures (the red team found
    it: an injected "5\u2011year warranty" slipped past).
    """
    return text.translate(_TYPOGRAPHY)


def extract_quantities(text: str) -> set[Quantity]:
    """Every number-with-unit in ``text``, normalised (days, weeks, months, years,
    hours, percent, GBP, USD, Rs). Typographic dashes and spaces are folded first."""
    text = fold_typography(text)
    found = {Quantity(_number(n), _TIME_UNITS[u.lower()]) for n, u in _TIME.findall(text)}
    found |= {Quantity(_number(n), "percent") for n in _PERCENT.findall(text)}
    found |= {Quantity(_number(n), _CURRENCIES[c.lower()]) for c, n in _MONEY_BEFORE.findall(text)}
    found |= {Quantity(_number(n), _CURRENCIES[c.lower()]) for n, c in _MONEY_AFTER.findall(text)}
    return found


def split_sentences(text: str) -> list[str]:
    """Sentences and list items, roughly: split after . ! ? and at line breaks."""
    return [s.strip() for s in _SENTENCE_END.split(text) if s.strip()]


def _contains(text: str, phrase: str) -> bool:
    """Case-insensitive substring match with whitespace and hyphens folded."""
    fold = lambda t: re.sub(r"[\s\-‐‑–—]+", " ", t.lower())  # noqa: E731
    return fold(phrase) in fold(text)


def _negated(sentence: str, phrase: str) -> bool:
    """True if ``phrase`` is denied in its own clause: a negation word among the few words
    just before it, with no comma, semicolon or "but" in between. "It does not have a
    lifetime warranty" is a denial; "I can't find it, but there's a lifetime warranty"
    is a claim."""
    lowered = sentence.lower()
    position = lowered.find(phrase.lower())
    if position <= 0:
        return False
    clause = _CLAUSE_BREAK.split(lowered[:position])[-1]
    return bool(_NEGATION.search(" ".join(clause.split()[-_NEGATION_WINDOW:])))


@dataclass(frozen=True)
class GuardVerdict:
    """The guard's decision and, when it fails, what failed (one line each)."""

    passed: bool
    problems: list[str] = field(default_factory=list)


class OutputGuard:
    """Checks an answer's numbers and claim phrases against its cited sources."""

    def __init__(
        self,
        claim_phrases: Sequence[str],
        attribution_terms: Sequence[str],
        voltcart_policy_terms: Sequence[str],
    ) -> None:
        """The phrase lists come from ``injection`` in ``configs/base.yaml``."""
        self._claims = tuple(claim_phrases)
        self._attribution = tuple(t.lower() for t in attribution_terms)
        self._policy = tuple(t.lower() for t in voltcart_policy_terms)

    def _sentence_problems(
        self,
        sentence: str,
        store: Sequence[RetrievedChunk],
        chunks: Sequence[RetrievedChunk],
        citations: Sequence[int],
    ) -> list[str]:
        """What in one sentence isn't supported by the sources it may rely on.

        Each problem names the figure or phrase and the cited source numbers that don't
        contain it, e.g. ``"15 days" is not in cited source [2]``.
        """
        problems = []
        sentence = fold_typography(sentence)
        lowered = sentence.lower()
        store_facts = set().union(*(extract_quantities(c.text) for c in store))
        cited_here = {int(n) for n in _MARKER.findall(sentence)}
        seller_facts: set[Quantity] = set()
        if any(term in lowered for term in self._attribution):
            for n in cited_here:
                if 1 <= n <= len(chunks) and chunks[n - 1].trust != "store":
                    seller_facts |= extract_quantities(chunks[n - 1].text)
        is_policy = any(term in lowered for term in self._policy)
        where = _cited_label(sorted(cited_here) or list(citations))
        for quantity in sorted(extract_quantities(sentence)):
            if quantity in store_facts:
                continue
            if quantity in seller_facts and not is_policy:
                continue
            if quantity in seller_facts:  # in the listing, but VoltCart policy needs store
                why = f"is a VoltCart policy figure, but only in a third-party listing ({where})"
            else:
                why = f"is not in {where}"
            problems.append(f'"{quantity.label}" {why} ("{sentence}")')
        for phrase in self._claims:
            claimed = _contains(sentence, phrase) and not _negated(sentence, phrase)
            if claimed and not any(_contains(c.text, phrase) for c in store):
                problems.append(f'"{phrase}" is not in a cited store source ("{sentence}")')
        return problems

    def check(
        self, answer: str, citations: Sequence[int], chunks: Sequence[RetrievedChunk]
    ) -> GuardVerdict:
        """Check ``answer`` against the cited sources among ``chunks`` (numbered from 1)."""
        cited = [chunks[n - 1] for n in citations if 1 <= n <= len(chunks)]
        store = [c for c in cited if c.trust == "store"]
        problems = [
            p for sentence in split_sentences(answer)
            for p in self._sentence_problems(sentence, store, chunks, citations)
        ]  # fmt: skip
        for problem in problems:
            logger.warning("OUTPUT GUARD: %s", problem)
        return GuardVerdict(passed=not problems, problems=problems)


def _cited_label(numbers: Sequence[int]) -> str:
    """``cited source [2]``, ``cited sources [1], [3]``, or ``any cited source``."""
    if not numbers:
        return "any cited source"
    refs = ", ".join(f"[{n}]" for n in numbers)
    return f"cited source{'s' if len(numbers) > 1 else ''} {refs}"

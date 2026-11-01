"""Tests for the query rewriter: prompt contents, validation, retry and fallback (no network)."""

from __future__ import annotations

import json
import logging

import pytest

from rag_assistant.config import load_prompts
from rag_assistant.query.rewrite import (
    Catalogue,
    InvalidRewriteError,
    QueryRewriter,
    Rewrite,
    countries_in,
    format_history,
    parse_rewrite,
    product_mentioned,
    rewrite_flags,
)
from rag_assistant.schemas import Product, Turn

CATALOGUE = Catalogue(
    (
        Product(code="AB-PRO", name="AeroBuds Pro Wireless Earbuds", category="Audio"),
        Product(code="NB-14", name="NoteBook 14 Laptop", category="Laptops"),
        Product(code="PB-20K", name="PowerBank 20K", category="Power and charging"),
        Product(
            code="QC-USBC",
            name="QuickCharge USB-C Cable (2 m, 100 W)",
            category="Power and charging",
        ),
        Product(code="XR-2200", name="XR-2200 Wi-Fi Router", category="Networking"),
    )
)
AEROBUDS_CHAT = [
    Turn(role="user", content="I'm thinking of buying the AeroBuds Pro."),
    Turn(role="assistant", content="AeroBuds Pro play for up to 8 hours."),
]
PROMPTS = load_prompts()
G027 = "What warranty comes with the QuickCharge USB-C cable?"


def _json(**overrides: object) -> str:
    """A valid rewriter reply, optionally changed."""
    reply = {
        "standalone_question": "Can I return the AeroBuds Pro?",
        "search_query": "Audio return window",
        "product_codes": ["AB-PRO"],
        "product_category": "Audio",
    }
    reply.update(overrides)
    return json.dumps(reply)


class FakeLLM:
    """Returns scripted replies in order and records every prompt it was sent."""

    def __init__(self, *replies: str) -> None:
        """Queue ``replies``."""
        self.replies = list(replies)
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        """The next scripted reply."""
        self.prompts.append(prompt)
        return self.replies.pop(0)


def _rewriter(llm: object, history_turns: int = 6) -> QueryRewriter:
    """A rewriter on the committed prompts with a fake LLM."""
    return QueryRewriter(
        llm,  # type: ignore[arg-type]
        PROMPTS["rewrite_query"],
        PROMPTS["rewrite_retry"],
        CATALOGUE,
        history_turns,
    )


# ------------------------------------------------------------------ prompt


def test_prompt_includes_history_catalogue_and_message() -> None:
    """The prompt shows the chat history, every catalogue line and the latest message."""
    prompt = _rewriter(FakeLLM()).prompt("Can I return it?", AEROBUDS_CHAT)
    assert "Customer: I'm thinking of buying the AeroBuds Pro." in prompt
    assert "Assistant: AeroBuds Pro play for up to 8 hours." in prompt
    assert all(f"{p.code} | {p.name} | {p.category}" in prompt for p in CATALOGUE.products)
    assert "Can I return it?" in prompt


def test_prompt_never_asks_for_region_or_audience() -> None:
    """The prompt forbids adding a country, and the output schema has no region field."""
    assert "Do not add a country" in PROMPTS["rewrite_query"]
    assert not {"region", "audience"} & set(Rewrite.model_fields)


def test_output_schema_is_frozen() -> None:
    """The schema sent to Groq is part of the cache key: the evaluated v4a/v4 runs used
    exactly these fields, this title and this description."""
    schema = Rewrite.model_json_schema()
    assert list(schema["properties"]) == [
        "standalone_question",
        "search_query",
        "product_codes",
        "product_category",
    ]
    assert schema["title"] == "Rewrite"
    assert schema["description"] == (
        "The rewriter's structured output. Deliberately has no region or audience field."
    )


def test_history_is_cut_to_the_latest_turns() -> None:
    """Only the last ``history_turns`` turns reach the prompt; none gives a placeholder."""
    turns = [Turn(role="user", content=f"message {n}") for n in range(10)]
    assert format_history(turns, 2) == "Customer: message 8\nCustomer: message 9"
    assert format_history([], 6) == "(no earlier messages)"
    assert format_history(turns, 0) == "(no earlier messages)"


# -------------------------------------------------------------- validation


def test_codes_are_canonicalised_and_null_category_is_fine() -> None:
    """Codes are upper-cased and de-duplicated."""
    rewrite = parse_rewrite(
        _json(product_codes=["ab-pro", "AB-PRO"], product_category=None), CATALOGUE
    )
    assert (rewrite.product_codes, rewrite.product_category) == (["AB-PRO"], None)


@pytest.mark.parametrize(
    "reply",
    [
        "not json",
        _json(product_codes=["ZZ-999"]),  # invented product code: not in the catalogue
        _json(product_category="Toasters"),  # not a catalogue category
        _json(region="UK"),  # the rewriter must never set a filter value
        _json(audience="internal"),
        _json(search_query=""),
        json.dumps({"standalone_question": "q"}),  # missing fields
    ],
)
def test_invalid_output_is_rejected(reply: str) -> None:
    """Anything outside the schema or the catalogue is invalid."""
    with pytest.raises(InvalidRewriteError):
        parse_rewrite(reply, CATALOGUE)


# ------------------------------------------------------ retry and fallback


def test_valid_reply_is_used_first_time() -> None:
    """One call, no fallback."""
    llm = FakeLLM(_json())
    result = _rewriter(llm).rewrite("Can I return it?", AEROBUDS_CHAT)
    assert (result.fallback, result.attempts, len(llm.prompts)) == (False, 1, 1)
    assert result.rewrite.product_codes == ["AB-PRO"]


def test_invalid_reply_is_retried_once_with_the_error() -> None:
    """The retry prompt adds the validation error, so it can't be a cache hit."""
    llm = FakeLLM(_json(product_codes=["ZZ-999"]), _json())
    result = _rewriter(llm).rewrite("Can I return it?", AEROBUDS_CHAT)
    assert (result.fallback, result.attempts) == (False, 2)
    assert llm.prompts[1].startswith(llm.prompts[0])
    assert "ZZ-999" in llm.prompts[1] and "invalid" in llm.prompts[1]


def test_two_invalid_replies_fall_back_to_the_raw_message(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """After the retry the raw message is used for both texts, and the fallback is logged."""
    llm = FakeLLM("garbage", _json(region="UK"))
    with caplog.at_level(logging.WARNING, logger="rag_assistant.query.rewrite"):
        result = _rewriter(llm).rewrite("Can I return it?", AEROBUDS_CHAT)
    assert (result.fallback, result.attempts, len(llm.prompts)) == (True, 2, 2)
    assert result.rewrite == Rewrite(
        standalone_question="Can I return it?", search_query="Can I return it?"
    )
    assert result.error and "region" in result.error
    assert "fell back to the raw message" in caplog.text


def test_api_rejection_counts_as_invalid_output() -> None:
    """An InvalidRewriteError raised by the completer (Groq HTTP 400) is retried too."""

    def rejecting(prompt: str) -> str:
        raise InvalidRewriteError("the API rejected the reply")

    assert _rewriter(rejecting).rewrite("hello").fallback


def test_other_errors_are_not_swallowed() -> None:
    """A network or quota error is not a fallback: it propagates."""

    def broken(prompt: str) -> str:
        raise RuntimeError("quota spent")

    with pytest.raises(RuntimeError):
        _rewriter(broken).rewrite("hello")


# ---------------------------------------------------------- rewrite checks


def test_product_mentioned_by_code_or_brand() -> None:
    """Codes match without hyphens; brands match across spaces ("power bank")."""
    pb, xr = CATALOGUE.by_code("PB-20K"), CATALOGUE.by_code("XR-2200")
    assert pb is not None and xr is not None
    assert product_mentioned(pb, "my power bank won't charge")
    assert product_mentioned(xr, "reset the xr2200")
    assert not product_mentioned(pb, "my laptop")


def test_countries_are_normalised_to_regions() -> None:
    """ "the United Kingdom" and "UK" are the same region; "us" as a pronoun is not the US."""
    assert countries_in("In the United Kingdom, or the UK") == {"UK"}
    assert countries_in("contact us in India") == {"IN"}
    assert countries_in("US customers") == {"US"}


def _flags(rewrite: Rewrite, message: str, history: list[Turn] | None = None) -> list[str]:
    """``rewrite_flags`` with codes found by a simple upper-case scan."""
    codes = [p.code for p in CATALOGUE.products]
    return rewrite_flags(
        rewrite,
        message,
        history or [],
        CATALOGUE,
        [c for c in codes if c in message],
        [c for c in codes if c in rewrite.search_query],
    )


def test_faithful_rewrite_from_history_is_not_flagged() -> None:
    """Naming the product discussed earlier is resolution, not invention."""
    assert _flags(parse_rewrite(_json(), CATALOGUE), "Can I return it?", AEROBUDS_CHAT) == []


def test_g027_dropped_product_is_flagged() -> None:
    """The evaluated g027 rewrite searched "Power and charging warranty": the QuickCharge
    cable the customer named is gone from the search, and the check reports it."""
    rewrite = Rewrite(
        standalone_question=G027,
        search_query="Power and charging warranty",
        product_codes=["QC-USBC"],
        product_category="Power and charging",
    )
    assert _flags(rewrite, G027) == ["dropped product QC-USBC"]
    kept = rewrite.model_copy(update={"search_query": "QuickCharge USB-C cable warranty"})
    assert _flags(kept, G027) == []


def test_invented_product_dropped_code_and_added_country_are_flagged() -> None:
    """A generic word turned into a catalogue product, a lost code and a new country."""
    rewrite = Rewrite(
        standalone_question="Can I return my opened NoteBook 14 in the UK?",
        search_query="Laptops return window opened",
        product_codes=["NB-14"],
    )
    message = "Can I return a laptop I've opened? It's next to my XR-2200."
    assert _flags(rewrite, message) == [
        "invented product NB-14",
        "dropped product XR-2200",
        "dropped code XR-2200",
        "added country UK",
    ]


def test_dropped_country_and_added_condition_are_flagged() -> None:
    """The customer's country missing from the search, or "opened" that nobody said."""
    rewrite = Rewrite(
        standalone_question="Can I send back the opened NoteBook 14 in the UK?",
        search_query="Laptops return window opened",
        product_codes=["NB-14"],
    )
    history = [Turn(role="user", content="I bought the NoteBook 14.")]
    assert _flags(rewrite, "Can I send it back? I'm in the UK.", history) == [
        "dropped country UK",
        "added condition opened",
    ]


def test_codes_kept_only_in_product_codes_are_not_dropped_codes() -> None:
    """A policy search may leave the code in product_codes only; that is a dropped
    product (the name is gone from the search) but not a dropped code."""
    rewrite = Rewrite(
        standalone_question="Can I return my NB-14?",
        search_query="Laptops return window",
        product_codes=["NB-14"],
    )
    assert _flags(rewrite, "Can I return my NB-14?") == ["dropped product NB-14"]

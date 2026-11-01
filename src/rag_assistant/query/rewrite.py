"""Query rewriting (Session 6): turn the latest message into searchable, standalone text.

The small model reads the chat history (the latest few turns), the latest message and a
compact product catalogue, then returns JSON validated by ``Rewrite``:

- ``standalone_question``: the message rewritten so it makes sense alone ("Can I return
  it?" after an AeroBuds Pro chat becomes "Can I return the AeroBuds Pro?"). It goes to
  the reranker and to generation.
- ``search_query``: text for hybrid search. For policy questions (returns, warranty,
  delivery, payment, discounts) it holds the category and policy terms instead of the
  product name, because policies list categories, not products; product questions keep
  the name and code (see ``rewrite_query`` in configs/prompts.yaml).
- ``product_codes`` and ``product_category``: catalogue values only.

Session 6 also tried composing ``search_query`` in code so that no product name could be
dropped. It lost 9-10 answers in retrieval, so it was rejected; the evaluation and the
reasons are in docs/failures/04-query-and-region.md. The ``dropped product`` check below
still reports every rewrite that leaves out a product the customer named.

The rewriter **never outputs region, audience or any filter value**. ``Rewrite`` has no
such field and forbids extra fields, so an output that tries is invalid. Region comes
from the user profile (``retrieval/filters.py``).

Invalid output (bad JSON, a code or category not in the catalogue, an extra field, or
Groq rejecting the JSON) is retried once with the error appended to the prompt. The error
changes the prompt, so the retry can't be answered from the cache. If the retry fails
too, the raw message is used unchanged and the fallback is logged and recorded.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

import groq
from langchain_groq import ChatGroq
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)

from rag_assistant.schemas import Product, Turn

logger = logging.getLogger(__name__)

# The LLM call: a prompt in, the reply text (expected to be JSON) out.
Completer = Callable[[str], str]

_ROLE_LABELS = {"user": "Customer", "assistant": "Assistant"}


class InvalidRewriteError(ValueError):
    """The model's reply could not be used as a ``Rewrite``."""


# Rewrite's JSON schema (fields, title and this class's docstring, which Pydantic copies
# into "description") is sent to Groq as the response format and is part of the LLM cache
# key. Changing any of them changes every stored rewrite.
class Rewrite(BaseModel):
    """The rewriter's structured output. Deliberately has no region or audience field."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    standalone_question: str = Field(min_length=1)
    search_query: str = Field(min_length=1)
    product_codes: list[str] = Field(default_factory=list)
    product_category: str | None = None

    @field_validator("product_codes")
    @classmethod
    def _canonical_codes(cls, value: list[str]) -> list[str]:
        """Upper-case and de-duplicate codes, keeping their order."""
        return list(dict.fromkeys(code.strip().upper() for code in value if code.strip()))

    @model_validator(mode="after")
    def _values_in_catalogue(self, info: ValidationInfo) -> Rewrite:
        """With a ``catalogue`` in the validation context, codes and category must be in it."""
        catalogue = (info.context or {}).get("catalogue")
        if catalogue is None:
            return self
        unknown = [c for c in self.product_codes if c not in catalogue.codes]
        if unknown:
            raise ValueError(f"product codes not in the catalogue: {unknown}")
        if self.product_category is not None and self.product_category not in catalogue.categories:
            raise ValueError(
                f"product_category {self.product_category!r} is not one of "
                f"{sorted(catalogue.categories)}"
            )
        return self


@dataclass(frozen=True)
class Catalogue:
    """The products the rewriter may name, built from the manifest's ``products``."""

    products: tuple[Product, ...]

    @property
    def codes(self) -> frozenset[str]:
        """Every catalogue product code."""
        return frozenset(p.code for p in self.products)

    @property
    def categories(self) -> frozenset[str]:
        """Every catalogue category (the return-table category names)."""
        return frozenset(p.category for p in self.products)

    def lines(self) -> str:
        """One ``code | name | category`` line per product, for the prompt."""
        return "\n".join(f"{p.code} | {p.name} | {p.category}" for p in self.products)

    def by_code(self, code: str) -> Product | None:
        """The product with ``code``, if any."""
        return next((p for p in self.products if p.code == code), None)

    def mentioned_in(self, text: str) -> list[Product]:
        """Products ``text`` names by code or brand word, in catalogue order."""
        return [p for p in self.products if product_mentioned(p, text)]


@dataclass(frozen=True)
class RewriteResult:
    """A rewrite plus how it was obtained (``fallback``: the raw message was used)."""

    rewrite: Rewrite
    fallback: bool
    attempts: int
    error: str | None = None


def format_history(history: Sequence[Turn], max_turns: int) -> str:
    """The latest ``max_turns`` turns as ``Customer: ...`` / ``Assistant: ...`` lines."""
    recent = list(history)[-max_turns:] if max_turns else []
    if not recent:
        return "(no earlier messages)"
    return "\n".join(f"{_ROLE_LABELS[t.role]}: {t.content}" for t in recent)


def build_rewrite_prompt(
    template: str,
    message: str,
    history: Sequence[Turn],
    catalogue: Catalogue,
    max_turns: int,
) -> str:
    """Fill the ``rewrite_query`` template with the catalogue, history and message."""
    return template.format(
        catalogue=catalogue.lines(),
        history=format_history(history, max_turns),
        message=message,
    )


def parse_rewrite(text: str, catalogue: Catalogue) -> Rewrite:
    """Validate the model's JSON reply against ``Rewrite`` and the catalogue."""
    try:
        return Rewrite.model_validate_json(text.strip(), context={"catalogue": catalogue})
    except ValidationError as exc:
        raise InvalidRewriteError(_short_error(exc)) from exc


def _short_error(exc: ValidationError) -> str:
    """A one-line summary of a validation error, for the retry prompt and the log."""
    return "; ".join(
        f"{'.'.join(str(p) for p in e['loc']) or 'reply'}: {e['msg']}" for e in exc.errors()
    )


def raw_rewrite(message: str) -> Rewrite:
    """The fallback: the raw message as both texts, with no codes or category."""
    return Rewrite(standalone_question=message, search_query=message)


class QueryRewriter:
    """Rewrites messages with an LLM, retrying invalid output and falling back to the raw text."""

    def __init__(
        self,
        complete: Completer,
        template: str,
        retry_template: str,
        catalogue: Catalogue,
        history_turns: int = 6,
        max_attempts: int = 2,
    ) -> None:
        """Use ``complete`` for LLM calls; ``retry_template`` gets ``{error}`` appended."""
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self._complete = complete
        self._template = template
        self._retry_template = retry_template
        self.catalogue = catalogue
        self._history_turns = history_turns
        self._max_attempts = max_attempts

    def prompt(self, message: str, history: Sequence[Turn] = ()) -> str:
        """The first-attempt prompt for ``message`` (shown by tests and tools)."""
        return build_rewrite_prompt(
            self._template, message, history, self.catalogue, self._history_turns
        )

    def rewrite(self, message: str, history: Sequence[Turn] = ()) -> RewriteResult:
        """Rewrite ``message`` given ``history``; never raises on invalid model output.

        Rate-limit exhaustion and network errors still raise: a fallback should mean
        "the model answered badly", not "the model was unreachable".
        """
        base = self.prompt(message, history)
        prompt, error = base, None
        for attempt in range(1, self._max_attempts + 1):
            try:
                rewrite = parse_rewrite(self._complete(prompt), self.catalogue)
                return RewriteResult(rewrite, fallback=False, attempts=attempt)
            except InvalidRewriteError as exc:
                error = str(exc)
                prompt = base + self._retry_template.format(error=error)
        logger.warning(
            "Query rewrite fell back to the raw message after %d attempts: %s",
            self._max_attempts,
            error,
        )
        return RewriteResult(
            raw_rewrite(message), fallback=True, attempts=self._max_attempts, error=error
        )


def json_completer(model: ChatGroq) -> Completer:
    """A ``Completer`` that asks Groq for JSON matching ``Rewrite``'s schema.

    Groq's structured outputs (``response_format: json_schema``) constrain the reply;
    Pydantic still validates it, because the catalogue rules aren't in the schema. A
    reply Groq itself rejects (HTTP 400) is treated as invalid output.
    """
    bound = model.bind(
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "rewrite", "schema": Rewrite.model_json_schema()},
        }
    )

    def complete(prompt: str) -> str:
        try:
            return str(bound.invoke(prompt).content)
        except groq.BadRequestError as exc:
            raise InvalidRewriteError(f"the API rejected the reply: {exc.message}") from exc

    return complete


# ------------------------------------------------------------- rewrite checks
# Rewriting can drop details or invent products. These checks need no LLM, so
# ``--rescore`` recomputes them; report.md counts and lists them.

_COUNTRY_PATTERNS = (
    ("IN", re.compile(r"\b(india|indian)\b", re.IGNORECASE)),
    ("UK", re.compile(r"\b(uk|u\.k\.|united kingdom|britain|british|england)\b", re.IGNORECASE)),
    ("US", re.compile(r"\b(usa|united states|america|american)\b", re.IGNORECASE)),
    ("US", re.compile(r"\b(US|U\.S\.)\b")),  # case-sensitive: "us" is usually a pronoun
)


def countries_in(text: str) -> set[str]:
    """Regions (IN, UK, US) that ``text`` names, e.g. "the United Kingdom" -> UK."""
    return {region for region, pattern in _COUNTRY_PATTERNS if pattern.search(text)}


def _squashed(text: str) -> str:
    """Lower-case with spaces and hyphens removed ("Power bank" -> "powerbank")."""
    return re.sub(r"[\s\-]+", "", text.lower())


def product_mentioned(product: Product, text: str) -> bool:
    """True if ``text`` names ``product`` by code or by the first word of its name.

    "power bank" counts for "PowerBank 20K" and "xr2200" for "XR-2200".
    """
    squashed = _squashed(text)
    brand = _squashed(product.name.split()[0])
    return _squashed(product.code) in squashed or brand in squashed


def rewrite_flags(
    rewrite: Rewrite,
    message: str,
    history: Iterable[Turn],
    catalogue: Catalogue,
    message_codes: Sequence[str],
    rewritten_codes: Sequence[str],
) -> list[str]:
    """Problems with a rewrite, as short labels (empty when it looks faithful).

    - ``invented product X``: X is in the rewrite but never named in the message or history.
    - ``dropped product X``: the message names product X (code or brand word) but
      ``search_query`` doesn't. Policy questions do this on purpose (the category
      replaces the name), and g027 shows the cost; see 04-query-and-region.md.
    - ``dropped code X``: a code in the message is in neither ``search_query`` nor
      ``product_codes``.
    - ``added country X``: a country appears in the rewrite but not in the conversation.
    - ``dropped country X``: a country in the message is missing from ``search_query``.
    - ``added condition X``: "opened", "unopened" or "sealed" appears in the rewrite but
      not in the conversation.

    ``message_codes`` / ``rewritten_codes`` are the codes ``CodeDetector`` finds in the
    message and in ``search_query``.
    """
    said = " ".join([message, *(t.content for t in history)])
    rewritten = f"{rewrite.standalone_question} {rewrite.search_query}"
    flags = []
    named = set(rewrite.product_codes) | {
        p.code for p in catalogue.products if product_mentioned(p, rewritten)
    }
    for code in sorted(named):
        product = catalogue.by_code(code)
        if product is not None and not product_mentioned(product, said):
            flags.append(f"invented product {code}")
    flags += [
        f"dropped product {p.code}"
        for p in catalogue.mentioned_in(message)
        if not product_mentioned(p, rewrite.search_query)
    ]
    kept = set(rewritten_codes) | set(rewrite.product_codes)
    flags += [f"dropped code {c}" for c in message_codes if c not in kept]
    flags += [f"added country {c}" for c in sorted(countries_in(rewritten) - countries_in(said))]
    missing = countries_in(message) - countries_in(rewrite.search_query)
    flags += [f"dropped country {c}" for c in sorted(missing)]
    added = conditions_in(rewritten) - conditions_in(said)
    flags += [f"added condition {c}" for c in sorted(added)]
    return flags


_CONDITION = re.compile(r"\b(opened|open|unopened|sealed)\b", re.IGNORECASE)


def conditions_in(text: str) -> set[str]:
    """Item conditions that ``text`` states: opened (also "open"), unopened or sealed."""
    found = {m.group(1).lower() for m in _CONDITION.finditer(text)}
    return {"opened" if c == "open" else c for c in found}

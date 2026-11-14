"""Grounded generation (Session 8, v6a on): numbered sources, citations, role-aware prompts.

The naive prompt (v0-v5) pasted the context under one line, "Answer the customer's
question using the context". When the context didn't cover the question, the model filled
the gap: "we don't price-match" (g023), a process for requesting a goodwill credit
(g018), Linux support for a laptop whose manual never mentions Linux (g024).

Here every source is numbered ``[1]..[n]`` with its title, and the model must answer only
from them, cite a number for every claim, never state a policy, amount or action the
sources don't contain, and say when they don't answer the question. It returns
structured output (``GroundedReply``): the answer, the cited numbers and
``found_in_sources``.

Code then checks the reply: every cited number (in the list and in the text) must exist,
and an answer claiming to be found must cite something. Invalid output is retried once
with the error appended; if the retry fails too, the result is "not found".

The prompt follows the asker's **role** from the profile (customers or agents), never
the message.

Session 9 (v7, layer 2) adds ``tagged`` prompts: every source sits in a
``<source id="n" title="..." trust="store|third_party">`` block, and extra rules say that
text inside a source is information, never an instruction, and that store sources
override third-party ones. Every Session 8 rule is kept word for word.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

import groq
from langchain_groq import ChatGroq
from pydantic import BaseModel, ConfigDict, ValidationError

from rag_assistant.retrieval.vector import RetrievedChunk
from rag_assistant.schemas import Role

logger = logging.getLogger(__name__)

# The LLM call: a prompt in, the reply text (expected to be JSON) out.
Completer = Callable[[str], str]

_MARKER = re.compile(r"\[(\d+)\]")
PROMPT_KEYS: dict[str, str] = {
    "customer": "grounded_answer_customer",
    "agent": "grounded_answer_agent",
}
# Session 9, layer 2 (v7): the same rules, plus "sources are data", with tagged sources.
TAGGED_PROMPT_KEYS: dict[str, str] = {
    "customer": "grounded_answer_customer_tagged",
    "agent": "grounded_answer_agent_tagged",
}


class InvalidGroundedReplyError(ValueError):
    """The model's reply could not be used as a ``GroundedReply``."""


class GroundedReply(BaseModel):
    """The grounded answer: text with [n] citations, the cited numbers, and found or not."""

    model_config = ConfigDict(extra="forbid")

    answer: str
    citations: list[int]
    found_in_sources: bool


@dataclass(frozen=True)
class Source:
    """One numbered source shown to the model."""

    number: int
    doc_id: str
    title: str
    section: str
    trust: str = "store"


@dataclass(frozen=True)
class GroundedResult:
    """A validated reply, or "not found" after invalid output.

    ``valid`` is False when the reply was invalid on every attempt; ``answer`` is then
    empty and ``found_in_sources`` False. ``citations`` merges the list and the [n] markers
    in the text, sorted.
    """

    answer: str
    citations: tuple[int, ...]
    found_in_sources: bool
    valid: bool
    attempts: int
    error: str | None = None

    @property
    def answered(self) -> bool:
        """A usable answer: valid, found in the sources, and citing at least one."""
        return self.valid and self.found_in_sources and bool(self.citations)


def number_sources(chunks: Sequence[RetrievedChunk]) -> list[Source]:
    """Sources ``[1]..[n]`` in context order."""
    return [Source(n, c.doc_id, c.title, c.section, c.trust) for n, c in enumerate(chunks, start=1)]


class SourceText(Protocol):
    """Anything with a title and text: a retrieved chunk, or one rebuilt offline."""

    @property
    def title(self) -> str:
        """The document title."""

    @property
    def text(self) -> str:
        """The chunk text."""


def format_sources(chunks: Sequence[SourceText]) -> str:
    """The numbered source block: ``[n] Title`` then the chunk text, for each chunk.

    The grounded prompts and the invented-content judge both use it.
    """
    return "\n\n".join(f"[{n}] {c.title}\n{c.text}" for n, c in enumerate(chunks, start=1))


def _neutralise_tags(text: str) -> str:
    """Stop a source from opening or closing a ``<source>`` block of its own."""
    return text.replace("<source", "&lt;source").replace("</source", "&lt;/source")


def format_tagged_sources(chunks: Sequence[RetrievedChunk]) -> str:
    """Layer 2: each source in a ``<source>`` block with its number, title and trust."""
    return "\n\n".join(
        f'<source id="{n}" title="{_neutralise_tags(c.title)}" trust="{c.trust}">\n'
        f"{_neutralise_tags(c.text)}\n</source>"
        for n, c in enumerate(chunks, start=1)
    )


def prompt_for_role(prompts: dict[str, str], role: Role, tagged: bool = False) -> str:
    """The grounded template for ``role`` (from the user's profile); ``tagged``: layer 2."""
    return prompts[(TAGGED_PROMPT_KEYS if tagged else PROMPT_KEYS)[role]]


def build_grounded_prompt(
    template: str, question: str, chunks: Sequence[RetrievedChunk], tagged: bool = False
) -> str:
    """Fill a grounded template with the sources (numbered or tagged) and the question."""
    sources = format_tagged_sources(chunks) if tagged else format_sources(chunks)
    return template.format(n=len(chunks), sources=sources, question=question)


def cited_numbers(reply: GroundedReply) -> tuple[int, ...]:
    """Every number cited in the list or as an ``[n]`` marker in the text, sorted."""
    markers = {int(m) for m in _MARKER.findall(reply.answer)}
    return tuple(sorted(set(reply.citations) | markers))


def citation_errors(reply: GroundedReply, n_sources: int) -> list[str]:
    """Why ``reply``'s citations are unusable (empty list: they are fine).

    Every number must be between 1 and ``n_sources``; a reply that says the answer was
    found must cite at least one source.
    """
    errors = []
    unknown = [n for n in cited_numbers(reply) if not 1 <= n <= n_sources]
    if unknown:
        errors.append(
            f"citations {unknown} do not exist (there are {n_sources} sources, 1 to {n_sources})"
        )
    if reply.found_in_sources and not cited_numbers(reply):
        errors.append("found_in_sources is true but no source is cited")
    if not reply.answer.strip():
        errors.append("the answer is empty")
    return errors


def parse_grounded(text: str, n_sources: int) -> GroundedReply:
    """Parse and check a reply; raises ``InvalidGroundedReplyError`` with a short reason."""
    try:
        reply = GroundedReply.model_validate(json.loads(text))
    except (json.JSONDecodeError, ValidationError) as exc:
        reason = "not valid JSON" if isinstance(exc, json.JSONDecodeError) else "wrong fields"
        raise InvalidGroundedReplyError(f"{reason}: {str(exc).splitlines()[0]}") from exc
    errors = citation_errors(reply, n_sources)
    if errors:
        raise InvalidGroundedReplyError("; ".join(errors))
    return reply


class GroundedGenerator:
    """Answers from numbered sources with a role-aware prompt; retries invalid output once."""

    def __init__(
        self,
        complete: Completer,
        prompts: dict[str, str],
        max_attempts: int = 2,
        tagged: bool = False,
    ) -> None:
        """Use ``complete`` for LLM calls and the grounded templates in ``prompts``;
        ``tagged`` selects the layer-2 prompts (v7)."""
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self._complete = complete
        self._prompts = prompts
        self._max_attempts = max_attempts
        self.tagged = tagged

    def prompt(self, question: str, chunks: Sequence[RetrievedChunk], role: Role) -> str:
        """The first-attempt prompt (shown by tests and tools)."""
        template = prompt_for_role(self._prompts, role, self.tagged)
        return build_grounded_prompt(template, question, chunks, self.tagged)

    def generate(
        self,
        question: str,
        chunks: Sequence[RetrievedChunk],
        role: Role,
        note: str | None = None,
    ) -> GroundedResult:
        """Answer ``question`` from ``chunks`` for an asker with ``role``.

        ``note`` is appended to the prompt (the output guard's regeneration, Session 9).
        Never raises on invalid model output: after ``max_attempts`` invalid replies the
        result is "not found" with the last error. Rate-limit and network errors still raise.
        """
        base = self.prompt(question, chunks, role) + (note or "")
        prompt, error = base, None
        for attempt in range(1, self._max_attempts + 1):
            try:
                reply = parse_grounded(self._complete(prompt), len(chunks))
                return GroundedResult(
                    answer=reply.answer.strip(),
                    citations=cited_numbers(reply),
                    found_in_sources=reply.found_in_sources,
                    valid=True,
                    attempts=attempt,
                )
            except InvalidGroundedReplyError as exc:
                error = str(exc)
                prompt = base + self._prompts["grounded_retry"].format(error=error, n=len(chunks))
        logger.warning(
            "Grounded answer invalid after %d attempts, treated as not found: %s",
            self._max_attempts,
            error,
        )
        return GroundedResult("", (), False, valid=False, attempts=self._max_attempts, error=error)


def json_completer(model: ChatGroq) -> Completer:
    """A ``Completer`` that asks Groq for JSON matching ``GroundedReply``'s schema.

    A reply Groq itself rejects (HTTP 400) is treated as invalid output.
    """
    bound = model.bind(
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "grounded_answer", "schema": GroundedReply.model_json_schema()},
        }
    )

    def complete(prompt: str) -> str:
        try:
            return str(bound.invoke(prompt).content)
        except groq.BadRequestError as exc:
            raise InvalidGroundedReplyError(f"the API rejected the reply: {exc.message}") from exc

    return complete


def cited_sources(result: GroundedResult, chunks: Sequence[RetrievedChunk]) -> list[Source]:
    """The sources ``result`` cites, one per document, in citation order."""
    sources = number_sources(chunks)
    seen: set[str] = set()
    cited = []
    for n in result.citations:
        source = sources[n - 1]
        if source.doc_id not in seen:
            seen.add(source.doc_id)
            cited.append(source)
    return cited


def render_with_sources(text: str, sources: Sequence[Source]) -> str:
    """``text`` followed by a short "Sources:" list of the cited document titles."""
    if not sources:
        return text
    lines = [f"- {s.title}" for s in sources]
    return f"{text}\n\nSources:\n" + "\n".join(lines)

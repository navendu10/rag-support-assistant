"""Naive answer generation (v0): paste the context into a one-line instruction.

No grounding rules, no handoff instruction and no sanitising of retrieved text.
"""

from __future__ import annotations

from collections.abc import Sequence

from langchain_groq import ChatGroq

from rag_assistant.llm import complete
from rag_assistant.retrieval.vector import RetrievedChunk

CONTEXT_SEPARATOR = "\n\n"


def build_context(chunks: Sequence[RetrievedChunk]) -> str:
    """Join chunk texts exactly as retrieved."""
    return CONTEXT_SEPARATOR.join(c.text for c in chunks)


def build_prompt(template: str, question: str, chunks: Sequence[RetrievedChunk]) -> str:
    """Fill the naive template with the question and raw context."""
    return template.format(context=build_context(chunks), question=question)


class NaiveGenerator:
    """Answers with the large model and the naive prompt."""

    def __init__(self, model: ChatGroq, template: str) -> None:
        """Use ``model`` with the ``naive_answer`` prompt template."""
        self._model = model
        self._template = template

    def generate(self, question: str, chunks: Sequence[RetrievedChunk]) -> str:
        """Return the model's answer to ``question`` given ``chunks``."""
        return complete(self._model, build_prompt(self._template, question, chunks))

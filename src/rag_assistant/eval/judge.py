"""LLM-as-judge calls (small model) for correctness and handoff."""

from __future__ import annotations

from collections.abc import Sequence

from langchain_groq import ChatGroq

from rag_assistant.eval.metrics import Judgement, parse_verdict
from rag_assistant.llm import complete
from rag_assistant.schemas import GoldenItem, Turn

CORRECTNESS_LABELS = ("CORRECT", "INCORRECT")
HANDOFF_LABELS = ("HANDOFF", "ANSWERED")


def format_history(history: Sequence[Turn]) -> str:
    """Render earlier turns for the judge, or ``(none)``."""
    if not history:
        return "(none)"
    return "\n".join(f"{turn.role}: {turn.content}" for turn in history)


class Judge:
    """Grades answers with strict binary rubrics from ``configs/prompts.yaml``."""

    def __init__(self, model: ChatGroq, prompts: dict[str, str]) -> None:
        """Use ``model`` with the ``judge_correctness`` and ``judge_handoff`` prompts."""
        self._model = model
        self._correctness = prompts["judge_correctness"]
        self._handoff = prompts["judge_handoff"]

    def correctness(self, item: GoldenItem, answer: str) -> Judgement:
        """CORRECT / INCORRECT against the reference answer."""
        prompt = self._correctness.format(
            history=format_history(item.history),
            question=item.question,
            reference=item.reference_answer,
            answer=answer,
        )
        return parse_verdict(complete(self._model, prompt), CORRECTNESS_LABELS)

    def handoff(self, item: GoldenItem, answer: str) -> Judgement:
        """HANDOFF / ANSWERED for questions that should be passed to a human."""
        prompt = self._handoff.format(question=item.question, answer=answer)
        return parse_verdict(complete(self._model, prompt), HANDOFF_LABELS)

"""LLM-as-judge calls (small model) for correctness, handoff and leaks."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from langchain_groq import ChatGroq

from rag_assistant.eval.metrics import Judgement, parse_verdict
from rag_assistant.llm import complete
from rag_assistant.schemas import GoldenItem, Turn, load_manifest

CORRECTNESS_LABELS = ("CORRECT", "INCORRECT")
HANDOFF_LABELS = ("HANDOFF", "ANSWERED")
LEAK_LABELS = ("LEAK", "CLEAN")


def format_history(history: Sequence[Turn]) -> str:
    """Render earlier turns for the judge, or ``(none)``."""
    if not history:
        return "(none)"
    return "\n".join(f"{turn.role}: {turn.content}" for turn in history)


def strip_front_matter(text: str) -> str:
    """Markdown body without its leading ``---`` YAML front matter."""
    if not text.startswith("---"):
        return text
    parts = text.split("---", 2)
    return parts[2].strip() if len(parts) == 3 else text


def internal_reference(generated_dir: Path) -> str:
    """The full text of every internal document, as the leak judge's definition of
    "internal-only". The same text is used for every stage, whatever was retrieved."""
    docs = sorted(
        (d for d in load_manifest(generated_dir / "manifest.json") if d.audience == "internal"),
        key=lambda d: d.doc_id,
    )
    return "\n\n".join(
        f"--- {d.doc_id} ---\n"
        + strip_front_matter((generated_dir / d.source_path).read_text(encoding="utf-8"))
        for d in docs
    )


def needs_leak_judge(failure_tag: str, user_role: str) -> bool:
    """The leak judge runs on customer questions tagged leak (agents may see playbooks)."""
    return failure_tag == "leak" and user_role == "customer"


class Judge:
    """Grades answers with strict binary rubrics from ``configs/prompts.yaml``."""

    def __init__(self, model: ChatGroq, prompts: dict[str, str]) -> None:
        """Use ``model`` with the ``judge_correctness`` and ``judge_handoff`` prompts."""
        self._model = model
        self._correctness = prompts["judge_correctness"]
        self._handoff = prompts["judge_handoff"]
        self._leak = prompts["judge_leak"]

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

    def leak(self, question: str, answer: str, internal: str) -> Judgement:
        """LEAK / CLEAN: does ``answer`` reveal anything only the internal documents say?"""
        prompt = self._leak.format(internal=internal, question=question, answer=answer)
        return parse_verdict(complete(self._model, prompt), LEAK_LABELS)

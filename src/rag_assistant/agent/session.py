"""One chat conversation with the agent (Session 11): the client API for chat.py and the UI.

``ChatSession`` holds the signed-in user, the conversation's thread id and its history.
It is deliberately small so a Streamlit page (Session 16) can wire it to buttons:

- ``send(text)``: a new message. In v9 any draft still waiting is cancelled first
  ("superseded"); the reply's ``agent.pending_draft`` is the draft to show, if any.
- ``drafts()``: the draft waiting for approval (zero or one).
- ``approve(draft)`` / ``reject(draft)``: the user's decision on the draft **as shown**
  (its id and hash). Only these resume the paused graph; no message text ever does.
"""

from __future__ import annotations

from rag_assistant.agent.graph import AgentPipeline, ApprovalResult, new_thread_id
from rag_assistant.pipeline import Answer
from rag_assistant.schemas import Turn, User


class ChatSession:
    """A conversation between one signed-in user and an agent stage."""

    def __init__(self, pipeline: AgentPipeline, user: User, thread_id: str | None = None) -> None:
        """Start (or, with ``thread_id``, continue) a conversation for ``user``."""
        self.pipeline = pipeline
        self.user = user
        self.thread_id = thread_id or new_thread_id()
        self.history: list[Turn] = []

    def send(self, text: str) -> Answer:
        """Send a message and return the agent's reply (the history grows by one exchange)."""
        answer = self.pipeline.answer(text, self.user, self.history, thread_id=self.thread_id)
        self.history += [
            Turn(role="user", content=text),
            Turn(role="assistant", content=answer.text),
        ]
        return answer

    def drafts(self) -> list[dict]:
        """The draft waiting for this user's approval, as a list of zero or one."""
        pending = self.pipeline.pending(self.thread_id, self.user)
        return [] if pending is None else [pending]

    def approve(self, draft: dict) -> ApprovalResult:
        """Approve ``draft`` exactly as it was shown (its id and hash)."""
        return self.pipeline.approve(self.thread_id, self.user, draft["draft_id"], draft["hash"])

    def reject(self, draft: dict) -> ApprovalResult:
        """Discard ``draft``; nothing is submitted."""
        return self.pipeline.reject(self.thread_id, self.user, draft["draft_id"])

    def reset(self) -> None:
        """Start a new conversation (a new thread; any waiting draft stays unapproved)."""
        self.thread_id = new_thread_id()
        self.history = []

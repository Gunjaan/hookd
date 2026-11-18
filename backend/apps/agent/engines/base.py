"""Contracts for the AI behind the reply agent.

An AgentModel takes one turn of the agent loop (`apps.agent.loop`): it gets the constant system prompt and tools plus
the run's transcript, and answers with content blocks (text, thinking, tool_use) in the Claude Messages format. The
loop runs the tools; the model never touches the database or the platform.

A BrainLearner fills the brains on its own: it turns a post caption, or a reply the creator wrote in the inbox, into
short labelled facts that services store as LearnedFact rows.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class ModelStep:
    content: list[dict]  # assistant content blocks, sent back unchanged on the next turn
    stop_reason: str


class AgentModel(ABC):
    name: str

    @abstractmethod
    def step(self, *, system: str, tools: list[dict], messages: list[dict]) -> ModelStep:
        """One assistant turn for the transcript `messages` (alternating user and assistant turns)."""


@dataclass(frozen=True)
class LearnedFactDraft:
    scope: str  # FactScope value: about this post, or about the whole shop
    label: str
    text: str


class BrainLearner(ABC):
    name: str

    @abstractmethod
    def from_caption(self, caption: str) -> list[LearnedFactDraft]:
        """Facts a buyer might ask about, stated in a post caption."""

    @abstractmethod
    def from_reply(self, *, question: str, answer: str, post_caption: str) -> list[LearnedFactDraft]:
        """Facts the creator's answer to a comment states, so the next buyer asking the same gets it on its own."""

"""Contract every reply engine implements.

Services build an AgentContext from the comment and the account and video brains, ask the engine for an
AgentDecision, then send it when the engine is confident or hold it for review. The engine never sends anything.

A BrainLearner fills the brains on its own: it turns a post caption, or a reply the creator wrote in the inbox, into
short labelled facts that services store as LearnedFact rows.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class AgentContext:
    comment_text: str
    author_username: str
    account_username: str
    voice: str
    account_knowledge: str
    post_caption: str = ""
    post_knowledge: str = ""


@dataclass(frozen=True)
class AgentDecision:
    intent: str
    language: str
    reply_text: str
    confidence: float
    reason: str


class ReplyEngine(ABC):
    name: str

    @abstractmethod
    def decide(self, context: AgentContext) -> AgentDecision:
        """Write the reply and say how sure it is that the given facts fully answer the comment."""


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

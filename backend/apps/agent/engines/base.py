"""Contract every reply engine implements.

Services build an AgentContext from the comment and the account and video brains, ask the engine for an
AgentDecision, then send it when the engine is confident or hold it for review. The engine never sends anything.
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

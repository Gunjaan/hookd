"""Contract every reply engine implements.

Services build an AgentContext from the comment, the creator's rules and the account and video brains, ask the
engine for an AgentDecision, then apply the rule's action and the sending limits. The engine never sends anything.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass(frozen=True)
class RuleSpec:
    id: int
    name: str
    when: str
    keywords: list[str]
    action: str
    how_to_reply: str


@dataclass(frozen=True)
class AgentContext:
    comment_text: str
    author_username: str
    account_username: str
    voice: str
    account_knowledge: str
    post_caption: str = ""
    post_knowledge: str = ""
    # Ordered: post rules first, then account rules. The first matching rule wins.
    rules: list[RuleSpec] = field(default_factory=list)


@dataclass(frozen=True)
class AgentDecision:
    rule_id: int | None
    intent: str
    language: str
    reply_text: str
    confidence: float
    reason: str


class ReplyEngine(ABC):
    name: str

    @abstractmethod
    def decide(self, context: AgentContext) -> AgentDecision:
        """Pick the matching rule (or none) and write the reply."""

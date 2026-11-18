"""Contracts for the AI behind the reply agent.

An AgentModel takes one turn of the agent loop (`apps.agent.loop`): it gets the constant system prompt and tools plus
the run's transcript, and answers with content blocks (text, thinking, tool_use) in the Claude Messages format. The
loop runs the tools; the model never touches the database or the platform. It also decides, in one small call,
whether a first DM from a new person is meant for the shop.

A BrainLearner fills the brains on its own: it turns a post (caption, images, top comments with the seller's replies),
or a reply the creator wrote in the inbox, into short labelled facts that services store as LearnedFact rows.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from apps.integrations.providers.types import PostComment


@dataclass(frozen=True)
class ModelStep:
    content: list[dict]  # assistant content blocks, sent back unchanged on the next turn
    stop_reason: str


class AgentModel(ABC):
    name: str

    @abstractmethod
    def step(self, *, system: str, tools: list[dict], messages: list[dict]) -> ModelStep:
        """One assistant turn for the transcript `messages` (alternating user and assistant turns)."""

    @abstractmethod
    def is_shop_message(self, text: str) -> bool:
        """Whether a first DM from a new person is about the shop (a question, an order, support), not personal chat."""


@dataclass(frozen=True)
class LearnedFactDraft:
    scope: str  # FactScope value: about this post, or about the whole shop
    label: str
    text: str


@dataclass(frozen=True)
class PostReading:
    """Everything read from one post: its caption, its images as (bytes, mime type), and its top comments."""

    caption: str = ""
    images: list[tuple[bytes, str]] = field(default_factory=list)
    comments: list[PostComment] = field(default_factory=list)


@dataclass(frozen=True)
class PostLearning:
    facts: list[LearnedFactDraft]
    buyer_questions: list[str]  # short topics buyers ask about, never facts


class BrainLearner(ABC):
    name: str

    @abstractmethod
    def from_post(self, post: PostReading) -> PostLearning:
        """Facts a buyer might ask about, from the caption, the images and the seller's own comments, plus the
        topics buyers ask about in the comments."""

    @abstractmethod
    def from_reply(self, *, question: str, answer: str, post_caption: str) -> list[LearnedFactDraft]:
        """Facts the creator's answer to a comment states, so the next buyer asking the same gets it on its own."""

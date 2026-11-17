"""Engine interfaces for the post studio. Engines call an AI model and never touch the database."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from apps.core.gemini import TokenUsage


@dataclass(frozen=True)
class ProductPhoto:
    data: bytes
    mime_type: str = "image/jpeg"


@dataclass(frozen=True)
class ShotPlan:
    role: str
    prompt: str


@dataclass(frozen=True)
class BrainQuestion:
    """Something buyers will ask in the comments that only the seller knows. `scope` is "post" or "account"."""

    label: str
    question: str
    scope: str
    answer: str = ""


@dataclass(frozen=True)
class PostBrief:
    product: str
    branding: list[str]
    audience: str
    slides: list[ShotPlan]
    caption: str
    hashtags: list[str] = field(default_factory=list)
    questions: list[BrainQuestion] = field(default_factory=list)
    usage: TokenUsage | None = None


@dataclass(frozen=True)
class PostCopy:
    caption: str
    hashtags: list[str]
    usage: TokenUsage | None = None


@dataclass(frozen=True)
class RenderedImage:
    data: bytes
    usage: TokenUsage | None = None


class CopyEngine(ABC):
    """Reads the product photos once and plans the shots and the post copy in a single call."""

    @abstractmethod
    def plan(self, photos: list[ProductPhoto], request_text: str) -> PostBrief: ...

    @abstractmethod
    def rewrite(self, request_text: str) -> PostCopy: ...


class ImageEngine(ABC):
    """Renders one post image from the product photos and a prompt: encoded image bytes plus token usage."""

    @abstractmethod
    def render(self, photos: list[ProductPhoto], prompt: str) -> RenderedImage: ...

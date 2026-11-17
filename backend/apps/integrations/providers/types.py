"""Platform neutral data transfer objects returned by every provider."""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class TokenBundle:
    access_token: str
    expires_at: datetime | None = None
    refresh_token: str = ""
    scopes: list[str] = field(default_factory=list)
    platform_user_id: str = ""


@dataclass(frozen=True)
class AccountProfile:
    external_id: str
    username: str = ""
    display_name: str = ""
    avatar_url: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class IncomingComment:
    """A comment parsed from a platform webhook."""

    account_external_id: str
    external_id: str
    text: str = ""
    author_external_id: str = ""
    author_username: str = ""
    media_external_id: str = ""
    parent_external_id: str = ""
    commented_at: datetime | None = None
    payload: dict = field(default_factory=dict)


@dataclass(frozen=True)
class IncomingMessage:
    """A direct message parsed from a platform webhook: sent to the account, or by it (echo)."""

    account_external_id: str
    external_id: str
    participant_external_id: str  # the other person in the conversation
    is_from_account: bool = False
    kind: str = "text"  # MessageKind value
    text: str = ""
    story_external_id: str = ""  # the account's story a story reply answers
    media_url: str = ""  # story or attachment media, a CDN URL that expires
    reply_to_external_id: str = ""  # the message this one quotes
    sent_at: datetime | None = None
    payload: dict = field(default_factory=dict)


@dataclass(frozen=True)
class MediaItem:
    """One post, reel or story on a connected account. Counts are None when the platform hides them."""

    external_id: str
    media_type: str
    product_type: str
    caption: str = ""
    permalink: str = ""
    media_url: str = ""
    thumbnail_url: str = ""
    posted_at: datetime | None = None
    like_count: int | None = None
    comments_count: int | None = None


@dataclass(frozen=True)
class MediaPage:
    items: list[MediaItem]
    next_cursor: str = ""


@dataclass(frozen=True)
class Metric:
    """A single insight value. `unit` is "count" or "milliseconds"."""

    key: str
    label: str
    value: int
    unit: str = "count"


@dataclass(frozen=True)
class CommentPage:
    comments: list[IncomingComment]
    next_cursor: str = ""


@dataclass(frozen=True)
class PublishStatus:
    """State of a publish container on the platform."""

    state: str
    detail: str = ""


@dataclass(frozen=True)
class PublishQuota:
    used: int
    limit: int

    @property
    def remaining(self) -> int:
        return max(self.limit - self.used, 0)

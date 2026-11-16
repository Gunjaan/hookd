"""Contract every social platform provider implements.

Services and views only talk to this interface, so adding YouTube means
writing one new provider package and registering it in
settings.HOOKD_PROVIDERS. Content methods (media, insights, comments,
publishing) default to FeatureNotSupported so a provider only implements what
its platform offers; the UI gates them on `capabilities`.
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, ClassVar

from apps.integrations.constants import Capability
from apps.integrations.exceptions import FeatureNotSupported, ProviderNotConfigured, WebhookVerificationError
from apps.integrations.providers.types import (
    AccountProfile,
    CommentPage,
    IncomingComment,
    MediaItem,
    MediaPage,
    Metric,
    PublishQuota,
    PublishStatus,
    TokenBundle,
)

if TYPE_CHECKING:
    from apps.integrations.models import SocialAccount


class BaseProvider(ABC):
    platform: ClassVar[str]
    display_name: ClassVar[str]
    description: ClassVar[str] = ""
    capabilities: ClassVar[frozenset[Capability]] = frozenset()
    # False when tokens can only be renewed by reconnecting; the UI then hides "Refresh".
    supports_token_refresh: ClassVar[bool] = True

    # Configuration

    @abstractmethod
    def is_configured(self) -> bool:
        """True when the required app credentials are present in settings."""

    def ensure_configured(self) -> None:
        if not self.is_configured():
            raise ProviderNotConfigured(f"{self.display_name} credentials are missing on the server.")

    # OAuth

    @abstractmethod
    def build_authorization_url(self, state: str) -> str:
        """URL the creator is sent to for granting access."""

    @abstractmethod
    def exchange_code(self, code: str) -> TokenBundle:
        """Swap an OAuth code for the longest lived token the platform offers."""

    @abstractmethod
    def fetch_profile(self, access_token: str) -> AccountProfile:
        """Load the connected account's identity."""

    @abstractmethod
    def refresh_access_token(self, account: "SocialAccount") -> TokenBundle:
        """Return a fresh token for an account whose token is close to expiry."""

    def on_account_connected(self, account: "SocialAccount") -> None:
        """Hook for post connect setup. Subscribes the account to webhooks when supported."""
        if self.supports_webhooks:
            self.subscribe_webhooks(account)

    # Webhooks. Providers without webhooks keep these defaults, which reject everything.

    @property
    def supports_webhooks(self) -> bool:
        return Capability.REALTIME_WEBHOOKS in self.capabilities

    def subscribe_webhooks(self, account: "SocialAccount") -> None:
        """Ask the platform to send this account's events to Hookd, using the stored token."""
        raise NotImplementedError

    def verify_webhook_challenge(self, params) -> str:
        """Answer the platform's subscription handshake. Returns the body to echo back."""
        raise WebhookVerificationError()

    def verify_webhook_signature(self, body: bytes, headers) -> bool:
        """True when the request really comes from the platform."""
        return False

    def parse_webhook_comments(self, payload: dict) -> list[IncomingComment]:
        """Extract new comments from a verified webhook payload."""
        return []

    # Content

    def list_media(self, account: "SocialAccount", *, limit: int, after: str = "") -> MediaPage:
        """Newest first page of the account's posts. `after` is the cursor from the previous page."""
        raise FeatureNotSupported()

    def get_media(self, account: "SocialAccount", media_external_id: str) -> MediaItem:
        """One post by its platform id."""
        raise FeatureNotSupported()

    def list_comments(self, account: "SocialAccount", media_external_id: str, *, limit: int, after: str = "") -> CommentPage:
        """One page of top level comments on a post."""
        raise FeatureNotSupported()

    # Replies (Capability.PUBLIC_REPLY)

    def reply_text_problems(self, text: str) -> list[str]:
        """Reasons the platform would reject `text` as a comment reply. Empty when it is fine."""
        return []

    def reply_to_comment(self, account: "SocialAccount", comment_external_id: str, message: str) -> str:
        """Post a public reply under a comment. Returns the new reply's platform id."""
        raise FeatureNotSupported()

    # Insights (Capability.INSIGHTS)

    def fetch_media_insights(self, account: "SocialAccount", media_external_id: str, product_type: str) -> list[Metric]:
        """Lifetime metrics for one post. `product_type` is a ProductType value."""
        raise FeatureNotSupported()

    def fetch_account_insights(self, account: "SocialAccount", *, days: int) -> list[Metric]:
        """Totals for the account over the last `days` days."""
        raise FeatureNotSupported()

    # Publishing (Capability.PUBLISH)

    def publishing_quota(self, account: "SocialAccount") -> PublishQuota:
        """How many posts the account may still publish through the API right now."""
        raise FeatureNotSupported()

    def start_publish(self, account: "SocialAccount", *, kind: str, media_urls: list[str], caption: str) -> str:
        """Hand public media URLs to the platform. Returns an id to poll with `publish_status`."""
        raise FeatureNotSupported()

    def publish_status(self, account: "SocialAccount", publish_id: str) -> PublishStatus:
        raise FeatureNotSupported()

    def finish_publish(self, account: "SocialAccount", publish_id: str) -> MediaItem:
        """Make a processed upload live. Returns the new post."""
        raise FeatureNotSupported()

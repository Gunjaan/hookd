"""Instagram provider using the Instagram API with Unified Login.

Creators log in with Instagram or Facebook on a Meta hosted page (Facebook
Login for Business with an "Instagram Login Compatible" configuration). No
Facebook Page is required. Flow:

1. Creator is sent to facebook.com/dialog/oauth with the Meta app ID and config_id.
2. Meta redirects to INSTAGRAM_REDIRECT_URI with ?code and ?state.
3. The code becomes a Business Integration System User Access Token, scoped to
   the creator's business portfolio. Its lifetime comes from the configuration.
4. /me/assigned_instagram_accounts lists the Instagram accounts the creator shared.
5. After connecting, the account is subscribed to the app's webhook fields.

Replies: POST /{comment_id}/replies posts a public reply under a comment.

Content: /{ig_id}/media lists posts, /{media_id}/insights and /{ig_id}/insights
return metrics, /{media_id}/comments pages comments. Publishing creates a
container from public media URLs, waits for status_code FINISHED, then calls
/{ig_id}/media_publish. Stories use the same flow with media_type STORIES.

Messages: with the `messages` webhook field subscribed, DMs, story replies and story mentions arrive under
entry[].messaging[] and are parsed by `parse_webhook_messages`.
"""

import hashlib
import hmac
import re
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

from django.conf import settings
from django.utils import timezone

from apps.integrations.constants import Capability, MediaType, MessageKind, Platform, ProductType, PublishKind, PublishState
from apps.integrations.exceptions import ProviderAPIError, TokenExpiredError, WebhookVerificationError
from apps.integrations.providers.base import BaseProvider
from apps.integrations.providers.instagram.client import LOGIN_DIALOG_URL, InstagramClient
from apps.integrations.providers.types import (
    AccountProfile,
    CommentPage,
    IncomingComment,
    IncomingMessage,
    MediaItem,
    MediaPage,
    Metric,
    PublishQuota,
    PublishStatus,
    TokenBundle,
)

PROFILE_FIELDS = (
    "id",
    "username",
    "name",
    "profile_picture_url",
    "followers_count",
    "media_count",
)

MEDIA_FIELDS = (
    "id",
    "caption",
    "media_type",
    "media_product_type",
    "media_url",
    "thumbnail_url",
    "permalink",
    "timestamp",
    "like_count",
    "comments_count",
)
COMMENT_FIELDS = ("id", "text", "timestamp", "username", "from", "parent_id")

MEDIA_TYPES = {
    "IMAGE": MediaType.IMAGE,
    "VIDEO": MediaType.VIDEO,
    "CAROUSEL_ALBUM": MediaType.CAROUSEL,
}
PRODUCT_TYPES = {
    "FEED": ProductType.FEED,
    "REELS": ProductType.REEL,
    "STORY": ProductType.STORY,
}

# Graph metric name, Hookd key, label, unit. Order is the display order.
FEED_METRICS = (
    ("views", "views", "Views", "count"),
    ("reach", "reach", "Reach", "count"),
    ("likes", "likes", "Likes", "count"),
    ("comments", "comments", "Comments", "count"),
    ("saved", "saves", "Saves", "count"),
    ("shares", "shares", "Shares", "count"),
    ("total_interactions", "interactions", "Interactions", "count"),
)
REEL_METRICS = FEED_METRICS + (
    ("ig_reels_avg_watch_time", "avg_watch_time", "Avg watch time", "milliseconds"),
    ("ig_reels_video_view_total_time", "total_watch_time", "Total watch time", "milliseconds"),
)
STORY_METRICS = (
    ("views", "views", "Views", "count"),
    ("reach", "reach", "Reach", "count"),
    ("replies", "replies", "Replies", "count"),
    ("shares", "shares", "Shares", "count"),
    ("total_interactions", "interactions", "Interactions", "count"),
)
MEDIA_METRICS = {
    ProductType.FEED: FEED_METRICS,
    ProductType.REEL: REEL_METRICS,
    ProductType.STORY: STORY_METRICS,
}
ACCOUNT_METRICS = (
    ("views", "views", "Views", "count"),
    ("reach", "reach", "Accounts reached", "count"),
    ("accounts_engaged", "engaged", "Accounts engaged", "count"),
    ("total_interactions", "interactions", "Interactions", "count"),
)

CONTAINER_STATES = {
    "FINISHED": PublishState.FINISHED,
    "PUBLISHED": PublishState.FINISHED,
    "IN_PROGRESS": PublishState.IN_PROGRESS,
    "ERROR": PublishState.FAILED,
    "EXPIRED": PublishState.FAILED,
}

# Webhook fields each connected account is subscribed to.

# Instagram rejects comments longer than 300 characters, with more than 4 hashtags or more than 1 URL,
# or written entirely in capitals.
REPLY_MAX_LENGTH = 300
REPLY_MAX_HASHTAGS = 4
REPLY_MAX_URLS = 1
HASHTAG_PATTERN = re.compile(r"#\w+")
URL_PATTERN = re.compile(r"(https?://|www\.)\S+", re.IGNORECASE)
SIGNATURE_HEADER = "X-Hub-Signature-256"


def _timestamp(value) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value), tz=UTC)
    except (TypeError, ValueError, OverflowError):
        return None


def _ms_timestamp(value) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=UTC)
    except (TypeError, ValueError, OverflowError):
        return None


def _message_kind(message: dict) -> str:
    attachments = message.get("attachments") or []
    if (message.get("reply_to") or {}).get("story"):
        return MessageKind.STORY_REPLY
    if any(item.get("type") == "story_mention" for item in attachments):
        return MessageKind.STORY_MENTION
    if message.get("is_unsupported"):
        return MessageKind.UNSUPPORTED
    return MessageKind.MEDIA if attachments else MessageKind.TEXT


def _iso_timestamp(value) -> datetime | None:
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S%z")
    except (TypeError, ValueError):
        return None


def _next_cursor(body: dict) -> str:
    # Graph sends cursors on the last page too; only "next" means more items exist.
    paging = body.get("paging") or {}
    return (paging.get("cursors") or {}).get("after", "") if paging.get("next") else ""


def _media_item(data: dict) -> MediaItem:
    return MediaItem(
        external_id=str(data["id"]),
        media_type=MEDIA_TYPES.get(data.get("media_type"), MediaType.IMAGE),
        product_type=PRODUCT_TYPES.get(data.get("media_product_type"), ProductType.FEED),
        caption=data.get("caption", ""),
        permalink=data.get("permalink", ""),
        media_url=data.get("media_url", ""),
        thumbnail_url=data.get("thumbnail_url", ""),
        posted_at=_iso_timestamp(data.get("timestamp")),
        like_count=data.get("like_count"),
        comments_count=data.get("comments_count"),
    )


def _metric_value(item: dict) -> int:
    # Media insights use values[0].value, account insights with metric_type=total_value use total_value.value.
    if isinstance(item.get("total_value"), dict):
        return int(item["total_value"].get("value") or 0)
    values = item.get("values") or [{}]
    return int(values[0].get("value") or 0)


def _metrics(body: dict, definitions) -> list[Metric]:
    returned = {item.get("name"): _metric_value(item) for item in body.get("data") or []}
    return [
        Metric(key=key, label=label, value=returned[name], unit=unit)
        for name, key, label, unit in definitions
        if name in returned
    ]


def _expiry_from_seconds(expires_in) -> datetime | None:
    try:
        seconds = int(expires_in)
    except (TypeError, ValueError):
        return None
    # Meta reports 0 for tokens that never expire.
    return timezone.now() + timedelta(seconds=seconds) if seconds > 0 else None


class InstagramProvider(BaseProvider):
    platform = Platform.INSTAGRAM.value
    display_name = "Instagram"
    description = "Creator or Business accounts. Reels, comments and DMs."
    supports_token_refresh = False
    capabilities = frozenset(
        {
            Capability.PUBLIC_REPLY,
            Capability.PRIVATE_REPLY,
            Capability.DIRECT_MESSAGE,
            Capability.HIDE_COMMENT,
            Capability.PUBLISH,
            Capability.REALTIME_WEBHOOKS,
            Capability.INSIGHTS,
        }
    )

    def __init__(self):
        self.config = settings.INSTAGRAM
        self.client = InstagramClient(
            api_version=self.config["GRAPH_API_VERSION"],
            timeout=settings.PROVIDER_HTTP_TIMEOUT_SECONDS,
        )

    def is_configured(self) -> bool:
        required = ("APP_ID", "APP_SECRET", "LOGIN_CONFIG_ID", "REDIRECT_URI")
        return all(self.config.get(key) for key in required)

    def build_authorization_url(self, state: str) -> str:
        # Permissions come from the login configuration, so no scope parameter is sent.
        # prompt=login always offers both Instagram and Facebook login, even when the
        # browser is already signed in to Facebook.
        query = {
            "client_id": self.config["APP_ID"],
            "config_id": self.config["LOGIN_CONFIG_ID"],
            "redirect_uri": self.config["REDIRECT_URI"],
            "response_type": "code",
            "override_default_response_type": "true",
            "prompt": "login",
            "state": state,
        }
        return f"{LOGIN_DIALOG_URL}?{urlencode(query)}"

    def exchange_code(self, code: str) -> TokenBundle:
        # Meta can append "#_=_" to the redirect URL.
        code = code.split("#", 1)[0]
        data = self.client.get(
            self.client.graph_url("oauth/access_token"),
            {
                "client_id": self.config["APP_ID"],
                "client_secret": self.config["APP_SECRET"],
                "redirect_uri": self.config["REDIRECT_URI"],
                "code": code,
            },
        )
        access_token = data.get("access_token")
        if not access_token:
            raise ProviderAPIError("Instagram did not return an access token.")
        return TokenBundle(
            access_token=access_token,
            expires_at=_expiry_from_seconds(data.get("expires_in")),
        )

    def fetch_profile(self, access_token: str) -> AccountProfile:
        # With this token /me is the business system user, not the Instagram account.
        system_user = self.client.get(
            self.client.graph_url("me"),
            {"fields": "id,client_business_id", "access_token": access_token},
        )
        assigned = self.client.get(
            self.client.graph_url("me/assigned_instagram_accounts"),
            {"fields": "id,username", "access_token": access_token},
        ).get("data") or []
        if not assigned:
            raise ProviderAPIError("No Instagram professional account was shared during login.")

        account_id = str(assigned[0]["id"])
        data = self.client.get(
            self.client.graph_url(account_id),
            {"fields": ",".join(PROFILE_FIELDS), "access_token": access_token},
        )
        return AccountProfile(
            external_id=account_id,
            username=data.get("username") or assigned[0].get("username", ""),
            display_name=data.get("name", ""),
            avatar_url=data.get("profile_picture_url", ""),
            metadata={
                "client_business_id": str(system_user.get("client_business_id", "")),
                "system_user_id": str(system_user.get("id", "")),
                "login_config_id": self.config["LOGIN_CONFIG_ID"],
                "assigned_account_ids": [str(item["id"]) for item in assigned],
                "followers_count": data.get("followers_count"),
                "media_count": data.get("media_count"),
            },
        )

    def refresh_access_token(self, account) -> TokenBundle:
        # Unified Login tokens are renewed by reconnecting. Use a configuration
        # whose token never expires to avoid this.
        raise TokenExpiredError("Instagram access is expiring. Please reconnect your account.")

    def subscribe_webhooks(self, account) -> None:
        data = self.client.post(
            self.client.graph_url(f"{account.external_id}/subscribed_apps"),
            {"subscribed_fields": ",".join(self.config.get("WEBHOOK_FIELDS") or ["comments"]), "access_token": account.access_token},
        )
        if not data.get("success"):
            raise ProviderAPIError("Instagram did not confirm the webhook subscription.")

    # Webhooks

    def verify_webhook_challenge(self, params) -> str:
        expected = self.config.get("WEBHOOK_VERIFY_TOKEN") or ""
        given = params.get("hub.verify_token") or ""
        if params.get("hub.mode") != "subscribe" or not expected or not hmac.compare_digest(given, expected):
            raise WebhookVerificationError()
        return params.get("hub.challenge") or ""

    def verify_webhook_signature(self, body: bytes, headers) -> bool:
        secret = self.config.get("APP_SECRET") or ""
        signature = headers.get(SIGNATURE_HEADER) or ""
        if not secret or not signature.startswith("sha256="):
            return False
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature.removeprefix("sha256="), expected)

    def parse_webhook_comments(self, payload: dict) -> list[IncomingComment]:
        if payload.get("object") != "instagram":
            return []
        comments = []
        for entry in payload.get("entry") or []:
            for change in entry.get("changes") or []:
                if change.get("field") != "comments":
                    continue
                value = change.get("value") or {}
                comment_id = value.get("comment_id") or value.get("id")
                if not comment_id:
                    continue
                author = value.get("from") or {}
                media = value.get("media") or {}
                comments.append(
                    IncomingComment(
                        account_external_id=str(entry.get("id", "")),
                        external_id=str(comment_id),
                        text=value.get("text", ""),
                        author_external_id=str(author.get("id", "")),
                        author_username=author.get("username", ""),
                        media_external_id=str(media.get("id", "")),
                        parent_external_id=str(value.get("parent_id") or ""),
                        commented_at=_timestamp(entry.get("time")),
                        payload=value,
                    )
                )
        return comments

    def parse_webhook_messages(self, payload: dict) -> list[IncomingMessage]:
        """`messages` field events under entry[].messaging[]. Echoes (sent by the account, `is_echo`) swap sender
        and recipient. Deleted messages and other events (reactions, reads, postbacks) are skipped."""
        if payload.get("object") != "instagram":
            return []
        messages = []
        for entry in payload.get("entry") or []:
            for event in entry.get("messaging") or []:
                message = event.get("message") or {}
                if not message.get("mid") or message.get("is_deleted"):
                    continue
                is_echo = bool(message.get("is_echo"))
                participant = (event.get("recipient") if is_echo else event.get("sender")) or {}
                reply_to = message.get("reply_to") or {}
                story = reply_to.get("story") or {}
                attachment = next(iter(message.get("attachments") or []), {})
                messages.append(
                    IncomingMessage(
                        account_external_id=str(entry.get("id", "")),
                        external_id=str(message["mid"]),
                        participant_external_id=str(participant.get("id", "")),
                        is_from_account=is_echo,
                        kind=_message_kind(message),
                        text=message.get("text", ""),
                        story_external_id=str(story.get("id", "")),
                        media_url=story.get("url") or (attachment.get("payload") or {}).get("url", ""),
                        reply_to_external_id=str(reply_to.get("mid") or ""),
                        sent_at=_ms_timestamp(event.get("timestamp")),
                        payload=event,
                    )
                )
        return messages

    # Content

    def list_media(self, account, *, limit: int, after: str = "") -> MediaPage:
        params = {"fields": ",".join(MEDIA_FIELDS), "limit": limit, "access_token": account.access_token}
        if after:
            params["after"] = after
        body = self.client.get(self.client.graph_url(f"{account.external_id}/media"), params)
        return MediaPage(items=[_media_item(item) for item in body.get("data") or []], next_cursor=_next_cursor(body))

    def get_media(self, account, media_external_id: str) -> MediaItem:
        data = self.client.get(
            self.client.graph_url(media_external_id),
            {"fields": ",".join(MEDIA_FIELDS), "access_token": account.access_token},
        )
        return _media_item(data)

    def list_comments(self, account, media_external_id: str, *, limit: int, after: str = "") -> CommentPage:
        params = {"fields": ",".join(COMMENT_FIELDS), "limit": limit, "access_token": account.access_token}
        if after:
            params["after"] = after
        body = self.client.get(self.client.graph_url(f"{media_external_id}/comments"), params)
        comments = []
        for item in body.get("data") or []:
            author = item.get("from") or {}
            comments.append(
                IncomingComment(
                    account_external_id=account.external_id,
                    external_id=str(item["id"]),
                    text=item.get("text", ""),
                    author_external_id=str(author.get("id", "")),
                    author_username=author.get("username") or item.get("username", ""),
                    media_external_id=media_external_id,
                    parent_external_id=str(item.get("parent_id") or ""),
                    commented_at=_iso_timestamp(item.get("timestamp")),
                    payload=item,
                )
            )
        return CommentPage(comments=comments, next_cursor=_next_cursor(body))

    # Insights

    def fetch_media_insights(self, account, media_external_id: str, product_type: str) -> list[Metric]:
        definitions = MEDIA_METRICS.get(product_type, FEED_METRICS)
        body = self.client.get(
            self.client.graph_url(f"{media_external_id}/insights"),
            {"metric": ",".join(name for name, *_ in definitions), "access_token": account.access_token},
        )
        return _metrics(body, definitions)

    def fetch_account_insights(self, account, *, days: int) -> list[Metric]:
        until = timezone.now()
        body = self.client.get(
            self.client.graph_url(f"{account.external_id}/insights"),
            {
                "metric": ",".join(name for name, *_ in ACCOUNT_METRICS),
                "period": "day",
                "metric_type": "total_value",
                "since": int((until - timedelta(days=days)).timestamp()),
                "until": int(until.timestamp()),
                "access_token": account.access_token,
            },
        )
        return _metrics(body, ACCOUNT_METRICS)

    # Publishing

    def publishing_quota(self, account) -> PublishQuota:
        body = self.client.get(
            self.client.graph_url(f"{account.external_id}/content_publishing_limit"),
            {"fields": "config,quota_usage", "access_token": account.access_token},
        )
        data = (body.get("data") or [{}])[0]
        return PublishQuota(
            used=int(data.get("quota_usage") or 0),
            limit=int((data.get("config") or {}).get("quota_total") or 0),
        )

    def start_publish(self, account, *, kind: str, media_urls: list[str], caption: str) -> str:
        if kind == PublishKind.IMAGE:
            return self._create_container(account, {"image_url": media_urls[0], "caption": caption})
        if kind == PublishKind.REEL:
            return self._create_container(account, {"media_type": "REELS", "video_url": media_urls[0], "caption": caption})
        if kind == PublishKind.CAROUSEL:
            children = [self._create_container(account, {"image_url": url, "is_carousel_item": "true"}) for url in media_urls]
            return self._create_container(
                account, {"media_type": "CAROUSEL", "children": ",".join(children), "caption": caption}
            )
        # Stories take no caption, and the API cannot add stickers, links or polls.
        if kind == PublishKind.STORY:
            return self._create_container(account, {"media_type": "STORIES", "image_url": media_urls[0]})
        if kind == PublishKind.STORY_VIDEO:
            return self._create_container(account, {"media_type": "STORIES", "video_url": media_urls[0]})
        raise ProviderAPIError(f"Instagram cannot publish {kind}.")

    def publish_status(self, account, publish_id: str) -> PublishStatus:
        body = self.client.get(
            self.client.graph_url(publish_id),
            {"fields": "status_code,status", "access_token": account.access_token},
        )
        state = CONTAINER_STATES.get(body.get("status_code"), PublishState.IN_PROGRESS)
        return PublishStatus(state=state, detail=body.get("status", "") if state == PublishState.FAILED else "")

    def finish_publish(self, account, publish_id: str) -> MediaItem:
        body = self.client.post(
            self.client.graph_url(f"{account.external_id}/media_publish"),
            {"creation_id": publish_id, "access_token": account.access_token},
        )
        media_id = body.get("id")
        if not media_id:
            raise ProviderAPIError("Instagram did not return the published post.")
        return self.get_media(account, str(media_id))

    # Replies

    def reply_text_problems(self, text: str) -> list[str]:
        problems = []
        if not text.strip():
            problems.append("The reply is empty.")
        if len(text) > REPLY_MAX_LENGTH:
            problems.append(f"Instagram replies can be at most {REPLY_MAX_LENGTH} characters.")
        if len(HASHTAG_PATTERN.findall(text)) > REPLY_MAX_HASHTAGS:
            problems.append(f"Instagram replies can have at most {REPLY_MAX_HASHTAGS} hashtags.")
        if len(URL_PATTERN.findall(text)) > REPLY_MAX_URLS:
            problems.append("Instagram replies can have at most 1 link.")
        letters = [char for char in text if char.isalpha()]
        if len(letters) > 3 and all(char.isupper() for char in letters):
            problems.append("Instagram rejects replies written entirely in capitals.")
        return problems

    def reply_to_comment(self, account, comment_external_id: str, message: str) -> str:
        body = self.client.post(
            self.client.graph_url(f"{comment_external_id}/replies"),
            {"message": message, "access_token": account.access_token},
        )
        if not body.get("id"):
            raise ProviderAPIError("Instagram did not accept the reply.")
        return str(body["id"])

    def _create_container(self, account, fields: dict) -> str:
        body = self.client.post(
            self.client.graph_url(f"{account.external_id}/media"),
            {**fields, "access_token": account.access_token},
        )
        if not body.get("id"):
            raise ProviderAPIError("Instagram did not accept the upload.")
        return str(body["id"])

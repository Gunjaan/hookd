"""Business logic for connecting and maintaining social accounts.

Views and management commands call these functions. They never call a
provider or the ORM directly.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import timedelta

from django.utils import timezone

from apps.comments.services import record_incoming_comments
from apps.messaging.services import record_incoming_messages
from apps.integrations import model_querysets as queries
from apps.integrations.exceptions import (
    AccountAlreadyLinked,
    AccountNotFound,
    ProviderAPIError,
    TokenExpiredError,
    WebhooksNotSupported,
    WebhookVerificationError,
)
from apps.integrations.models import SocialAccount
from apps.integrations.oauth_state import issue_state, validate_state
from apps.integrations.providers.base import BaseProvider
from apps.integrations.providers.registry import all_providers, get_provider

logger = logging.getLogger(__name__)

DEFAULT_REFRESH_WINDOW = timedelta(days=10)


def build_connect_url(*, user, platform: str) -> str:
    provider = get_provider(platform)
    provider.ensure_configured()
    return provider.build_authorization_url(issue_state(user_id=user.pk, platform=platform))


def complete_connection(*, user, platform: str, code: str, state: str) -> SocialAccount:
    provider = get_provider(platform)
    provider.ensure_configured()
    validate_state(state, user_id=user.pk, platform=platform)

    tokens = provider.exchange_code(code)
    profile = provider.fetch_profile(tokens.access_token)

    existing = queries.find_account(platform, profile.external_id)
    if existing is not None and existing.user_id != user.pk:
        raise AccountAlreadyLinked()

    account = queries.upsert_account(user=user, platform=platform, profile=profile, tokens=tokens)

    try:
        provider.on_account_connected(account)
    except ProviderAPIError as exc:
        logger.warning("Post connect setup failed for account %s: %s", account.pk, exc.message)
        queries.set_account_status(account, SocialAccount.Status.ACTIVE, f"Webhook setup incomplete: {exc.message}")

    logger.info("Connected %s account %s for user %s", platform, account.external_id, user.pk)
    return account


def get_user_account(*, user, account_id: int) -> SocialAccount:
    account = queries.get_account_for_user(user, account_id)
    if account is None:
        raise AccountNotFound()
    return account


@dataclass
class ProviderOverview:
    """One platform on the connections page with the user's accounts on it."""

    provider: BaseProvider
    configured: bool
    capabilities: list[str]
    accounts: list[SocialAccount]


def connection_overview(user) -> list[ProviderOverview]:
    accounts = queries.list_accounts_for_user(user)
    return [
        ProviderOverview(
            provider=provider,
            configured=provider.is_configured(),
            capabilities=sorted(c.value for c in provider.capabilities),
            accounts=[account for account in accounts if account.platform == provider.platform],
        )
        for provider in all_providers()
    ]


def disconnect_account(*, user, account_id: int) -> SocialAccount:
    account = get_user_account(user=user, account_id=account_id)
    queries.delete_account(account)
    logger.info("Disconnected %s account %s for user %s", account.platform, account.external_id, user.pk)
    return account


def subscribe_account_webhooks(account: SocialAccount) -> SocialAccount:
    """Re-run the webhook subscription with the stored token. No platform login needed."""
    provider = get_provider(account.platform)
    if not provider.supports_webhooks:
        raise WebhooksNotSupported(f"{provider.display_name} does not support webhooks.")
    try:
        provider.subscribe_webhooks(account)
    except TokenExpiredError as exc:
        queries.set_account_status(account, SocialAccount.Status.EXPIRED, exc.message)
        raise
    except ProviderAPIError as exc:
        queries.set_account_status(account, SocialAccount.Status.ACTIVE, f"Webhook setup incomplete: {exc.message}")
        raise
    logger.info("Subscribed %s account %s to webhooks", account.platform, account.external_id)
    return queries.set_account_status(account, SocialAccount.Status.ACTIVE)


def refresh_account_token(account: SocialAccount) -> SocialAccount:
    provider = get_provider(account.platform)
    try:
        tokens = provider.refresh_access_token(account)
    except TokenExpiredError as exc:
        queries.set_account_status(account, SocialAccount.Status.EXPIRED, exc.message)
        raise
    except ProviderAPIError as exc:
        queries.set_account_status(account, SocialAccount.Status.ERROR, exc.message)
        raise
    return queries.save_refreshed_token(account, tokens)


@dataclass
class RefreshSummary:
    refreshed: list[int] = field(default_factory=list)
    failed: dict[int, str] = field(default_factory=dict)


def refresh_expiring_tokens(*, window: timedelta = DEFAULT_REFRESH_WINDOW, platform: str | None = None) -> RefreshSummary:
    summary = RefreshSummary()
    for account in queries.active_accounts_expiring_before(timezone.now() + window, platform):
        try:
            refresh_account_token(account)
        except ProviderAPIError as exc:
            summary.failed[account.pk] = exc.message
            logger.warning("Token refresh failed for account %s: %s", account.pk, exc.message)
        else:
            summary.refreshed.append(account.pk)
    return summary


def answer_webhook_challenge(*, platform: str, params) -> str:
    return get_provider(platform).verify_webhook_challenge(params)


def process_webhook(*, platform: str, body: bytes, headers) -> int:
    """Verify and handle one webhook delivery. Returns the number of new comments and messages stored."""
    provider = get_provider(platform)
    if not provider.verify_webhook_signature(body, headers):
        raise WebhookVerificationError("Invalid webhook signature.")
    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise WebhookVerificationError("Webhook body is not valid JSON.") from exc
    if not isinstance(payload, dict):
        raise WebhookVerificationError("Webhook body is not a JSON object.")
    return record_incoming_comments(
        platform=platform, comments=provider.parse_webhook_comments(payload)
    ) + record_incoming_messages(platform=platform, messages=provider.parse_webhook_messages(payload))


@dataclass
class SubscribeSummary:
    subscribed: list[int] = field(default_factory=list)
    failed: dict[int, str] = field(default_factory=dict)


def subscribe_all_webhooks(*, platform: str | None = None) -> SubscribeSummary:
    """Re-subscribe every active account, for example after adding a webhook field."""
    summary = SubscribeSummary()
    for account in queries.active_accounts(platform):
        if not get_provider(account.platform).supports_webhooks:
            continue
        try:
            subscribe_account_webhooks(account)
        except ProviderAPIError as exc:
            summary.failed[account.pk] = exc.message
        else:
            summary.subscribed.append(account.pk)
    return summary

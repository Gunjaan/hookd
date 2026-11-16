"""All database access for the integrations app lives here."""

from datetime import datetime

from django.db import connection
from django.db.models import QuerySet
from django.utils import timezone

from apps.integrations.models import SocialAccount
from apps.integrations.providers.types import AccountProfile, TokenBundle


def list_accounts_for_user(user) -> QuerySet[SocialAccount]:
    return SocialAccount.objects.filter(user=user).order_by("platform", "username")


def get_account_for_user(user, account_id: int) -> SocialAccount | None:
    return SocialAccount.objects.filter(user=user, pk=account_id).first()


def find_account(platform: str, external_id: str) -> SocialAccount | None:
    return SocialAccount.objects.filter(platform=platform, external_id=external_id).first()


def upsert_account(*, user, platform: str, profile: AccountProfile, tokens: TokenBundle) -> SocialAccount:
    metadata = dict(profile.metadata)
    if tokens.platform_user_id:
        metadata["platform_user_id"] = tokens.platform_user_id
    account, _ = SocialAccount.objects.update_or_create(
        platform=platform,
        external_id=profile.external_id,
        defaults={
            "user": user,
            "username": profile.username,
            "display_name": profile.display_name,
            "avatar_url": profile.avatar_url,
            "access_token": tokens.access_token,
            "refresh_token": tokens.refresh_token,
            "token_expires_at": tokens.expires_at,
            "scopes": tokens.scopes,
            "status": SocialAccount.Status.ACTIVE,
            "status_detail": "",
            "metadata": metadata,
            "last_refreshed_at": timezone.now(),
        },
    )
    return account


def save_refreshed_token(account: SocialAccount, tokens: TokenBundle) -> SocialAccount:
    account.access_token = tokens.access_token
    account.token_expires_at = tokens.expires_at
    if tokens.refresh_token:
        account.refresh_token = tokens.refresh_token
    account.status = SocialAccount.Status.ACTIVE
    account.status_detail = ""
    account.last_refreshed_at = timezone.now()
    account.save(
        update_fields=[
            "access_token",
            "refresh_token",
            "token_expires_at",
            "status",
            "status_detail",
            "last_refreshed_at",
            "updated_at",
        ]
    )
    return account


def set_account_status(account: SocialAccount, status: str, detail: str = "") -> SocialAccount:
    account.status = status
    account.status_detail = detail[:500]
    account.save(update_fields=["status", "status_detail", "updated_at"])
    return account


def delete_account(account: SocialAccount) -> None:
    account.delete()


def stored_access_token_ciphertext(account_id: int) -> str | None:
    """Raw column value, bypassing decryption. Used to audit encryption at rest.

    Raw SQL is required because ORM reads always run the field's decryption.
    """
    table = SocialAccount._meta.db_table
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT access_token FROM {table} WHERE id = %s", [account_id])
        result = cursor.fetchone()
    return result[0] if result else None


def active_accounts(platform: str | None = None) -> QuerySet[SocialAccount]:
    queryset = SocialAccount.objects.filter(status=SocialAccount.Status.ACTIVE)
    if platform:
        queryset = queryset.filter(platform=platform)
    return queryset.order_by("pk")


def active_accounts_expiring_before(moment: datetime, platform: str | None = None) -> QuerySet[SocialAccount]:
    queryset = SocialAccount.objects.filter(
        status=SocialAccount.Status.ACTIVE,
        token_expires_at__isnull=False,
        token_expires_at__lte=moment,
    )
    if platform:
        queryset = queryset.filter(platform=platform)
    return queryset.order_by("token_expires_at")

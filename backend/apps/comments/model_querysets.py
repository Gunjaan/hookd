"""All database access for the comments app lives here."""

from django.db.models import QuerySet

from apps.comments.models import Comment
from apps.integrations.models import SocialAccount
from apps.integrations.providers.types import IncomingComment


def create_comment_if_new(*, account: SocialAccount, incoming: IncomingComment) -> tuple[Comment, bool]:
    return Comment.objects.get_or_create(
        account=account,
        external_id=incoming.external_id,
        defaults={
            "media_external_id": incoming.media_external_id,
            "parent_external_id": incoming.parent_external_id,
            "author_external_id": incoming.author_external_id,
            "author_username": incoming.author_username,
            "text": incoming.text,
            "is_from_account": incoming.author_external_id == account.external_id,
            "commented_at": incoming.commented_at,
            "payload": incoming.payload,
        },
    )


def list_comments_for_account(account: SocialAccount) -> QuerySet[Comment]:
    return Comment.objects.filter(account=account)


def get_comment(comment_id: int) -> Comment | None:
    return Comment.objects.select_related("account").filter(pk=comment_id).first()


def latest_comment_by_author(account: SocialAccount, author_external_id: str) -> Comment | None:
    """The newest comment a person wrote on the account's posts (tells the agent a DM sender's username)."""
    return (
        Comment.objects.filter(account=account, author_external_id=author_external_id)
        .order_by("-commented_at", "-pk")
        .first()
    )

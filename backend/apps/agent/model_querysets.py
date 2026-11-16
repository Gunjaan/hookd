"""All database access for the agent app lives here."""

from datetime import datetime

from django.db.models import Count, F, QuerySet
from django.utils import timezone

from apps.agent.constants import ReplyStatus
from apps.agent.models import AgentProfile, CommentReply, PostBrain
from apps.comments.models import Comment
from apps.content.models import Post
from apps.integrations.models import SocialAccount

# Replies that count as going out (or gone out) for the sending limits.
OUTGOING_STATUSES = (ReplyStatus.QUEUED, ReplyStatus.SENDING, ReplyStatus.SENT)


# Profile

def get_profile(account: SocialAccount) -> AgentProfile | None:
    return AgentProfile.objects.filter(account=account).first()


def get_or_create_profile(account: SocialAccount) -> AgentProfile:
    profile, _ = AgentProfile.objects.get_or_create(account=account)
    return profile


def lock_profile(account: SocialAccount) -> AgentProfile:
    """Profile row locked for the current transaction (send slot reservation)."""
    get_or_create_profile(account)
    return AgentProfile.objects.select_for_update().get(account=account)


def update_profile(profile: AgentProfile, **fields) -> AgentProfile:
    for name, value in fields.items():
        setattr(profile, name, value)
    profile.save(update_fields=[*fields, "updated_at"])
    return profile


# Video brain

def get_post_brain(post: Post) -> PostBrain | None:
    return PostBrain.objects.filter(post=post).first()


def save_post_brain(post: Post, *, knowledge: str, agent_enabled: bool) -> PostBrain:
    brain, _ = PostBrain.objects.update_or_create(
        post=post, defaults={"knowledge": knowledge, "agent_enabled": agent_enabled}
    )
    return brain


# Replies

def reply_exists(comment: Comment) -> bool:
    return CommentReply.objects.filter(comment=comment).exists()


def create_reply_if_new(comment: Comment, **fields) -> tuple[CommentReply, bool]:
    return CommentReply.objects.get_or_create(comment=comment, defaults=fields)


def get_reply(reply_id: int) -> CommentReply | None:
    return CommentReply.objects.select_related("comment__account").filter(pk=reply_id).first()


def get_reply_for_user(user, reply_id: int) -> CommentReply | None:
    return CommentReply.objects.select_related("comment__account").filter(comment__account__user=user, pk=reply_id).first()


def update_reply(reply: CommentReply, **fields) -> CommentReply:
    for name, value in fields.items():
        setattr(reply, name, value)
    reply.save(update_fields=[*fields, "updated_at"])
    return reply


def claim_reply_for_sending(reply_id: int) -> bool:
    """Move a queued reply to sending. Only one caller can win, so a reply is never sent twice."""
    claimed = CommentReply.objects.filter(pk=reply_id, status=ReplyStatus.QUEUED).update(
        status=ReplyStatus.SENDING, attempts=F("attempts") + 1, updated_at=timezone.now()
    )
    return claimed == 1


def sent_times_since(account: SocialAccount, since: datetime) -> list[datetime]:
    """Send times of the account's replies since `since`, oldest first."""
    return list(
        CommentReply.objects.filter(comment__account=account, status=ReplyStatus.SENT, sent_at__gte=since)
        .order_by("sent_at")
        .values_list("sent_at", flat=True)
    )


def outgoing_texts_since(account: SocialAccount, since: datetime) -> list[str]:
    return list(
        CommentReply.objects.filter(comment__account=account, status__in=OUTGOING_STATUSES, updated_at__gte=since)
        .values_list("final_text", flat=True)
    )


def list_replies_for_user(user, status: str, limit: int) -> list[CommentReply]:
    return list(
        CommentReply.objects.filter(comment__account__user=user, status=status)
        .select_related("comment__account")
        .order_by("-created_at")[:limit]
    )


def reply_counts_for_user(user) -> dict[str, int]:
    rows = CommentReply.objects.filter(comment__account__user=user).values("status").annotate(total=Count("pk"))
    return {row["status"]: row["total"] for row in rows}


def pending_review_count(user) -> int:
    return CommentReply.objects.filter(comment__account__user=user, status=ReplyStatus.PENDING_REVIEW).count()


def overdue_queued_reply_ids(before: datetime) -> list[int]:
    return list(
        CommentReply.objects.filter(status=ReplyStatus.QUEUED, scheduled_for__lt=before).values_list("pk", flat=True)
    )

"""All database access for the agent app lives here."""

from datetime import datetime

from django.db import transaction
from django.db.models import Count, F, Q, QuerySet
from django.utils import timezone

from apps.agent.constants import ReplyStatus
from apps.agent.models import AgentProfile, CommentReply, LearnedFact, PostBrain
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


def claim_caption_learning(post: Post) -> bool:
    """Mark the post's caption as read. Only one caller ever wins, so a caption is read once."""
    PostBrain.objects.get_or_create(post=post)
    claimed = PostBrain.objects.filter(post=post, caption_learned_at__isnull=True).update(
        caption_learned_at=timezone.now(), updated_at=timezone.now()
    )
    return claimed == 1


def release_caption_learning(post: Post) -> None:
    """Undo the claim after a failed read, so the next comment on the post tries again."""
    PostBrain.objects.filter(post=post).update(caption_learned_at=None, updated_at=timezone.now())


# Learned facts

def learned_facts(account: SocialAccount, post: Post | None, limit: int) -> list[LearnedFact]:
    """The account's shop facts plus, when given, the post's facts. Newest first, at most `limit` of each."""
    shop = list(LearnedFact.objects.filter(account=account, post__isnull=True)[:limit])
    return shop + (list(LearnedFact.objects.filter(post=post)[:limit]) if post else [])


def account_learned_facts(account: SocialAccount, limit: int) -> list[LearnedFact]:
    return list(LearnedFact.objects.filter(account=account, post__isnull=True)[:limit])


def post_learned_facts(post: Post, limit: int) -> list[LearnedFact]:
    return list(LearnedFact.objects.filter(post=post)[:limit])


def post_learned_fact_count(account: SocialAccount) -> int:
    return LearnedFact.objects.filter(account=account, post__isnull=False).count()


def save_learned_facts(
    account: SocialAccount, facts: list[tuple[Post | None, str, str]], *, source: str, source_post: Post | None,
    source_reply: CommentReply | None = None,
) -> list[LearnedFact]:
    """Store (post, label, text) facts. An older fact on the same topic (same post or shop wide, same label) is
    replaced."""
    with transaction.atomic():
        for post, label, _ in facts:
            same_place = Q(post=post) if post else Q(post__isnull=True)
            LearnedFact.objects.filter(same_place, account=account, label__iexact=label).delete()
        return LearnedFact.objects.bulk_create(
            LearnedFact(
                account=account, post=post, label=label, text=text, source=source, source_post=source_post,
                source_reply=source_reply,
            )
            for post, label, text in facts
        )


def get_learned_fact_for_user(user, fact_id: int) -> LearnedFact | None:
    return LearnedFact.objects.filter(account__user=user, pk=fact_id).first()


def delete_learned_fact(fact: LearnedFact) -> None:
    fact.delete()


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
        .prefetch_related("learned_facts")
        .order_by("-created_at")[:limit]
    )


def reply_counts_for_user(user) -> dict[str, int]:
    rows = CommentReply.objects.filter(comment__account__user=user).values("status").annotate(total=Count("pk"))
    return {row["status"]: row["total"] for row in rows}


def sent_reply_count(account: SocialAccount, since: datetime | None = None) -> int:
    replies = CommentReply.objects.filter(comment__account=account, status=ReplyStatus.SENT)
    if since is not None:
        replies = replies.filter(sent_at__gte=since)
    return replies.count()


def pending_review_count(user) -> int:
    return CommentReply.objects.filter(comment__account__user=user, status=ReplyStatus.PENDING_REVIEW).count()


def overdue_queued_reply_ids(before: datetime) -> list[int]:
    return list(
        CommentReply.objects.filter(status=ReplyStatus.QUEUED, scheduled_for__lt=before).values_list("pk", flat=True)
    )

"""All database access for the agent app lives here."""

from datetime import datetime

from django.db import transaction
from django.db.models import Count, F, Q, QuerySet
from django.utils import timezone

from apps.agent.constants import FINISHED_RUN_STATUSES, EventKind, QuestionStatus, ReplyStatus, RunStatus, TicketStatus
from apps.agent.models import (
    AgentAction,
    AgentEvent,
    AgentProfile,
    AgentRun,
    CommentReply,
    LearnedFact,
    OutgoingDM,
    PostBrain,
    SellerQuestion,
    SupportTicket,
)
from apps.comments.models import Comment
from apps.content.models import Post
from apps.integrations.models import SocialAccount
from apps.messaging.models import Conversation

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


def claim_post_learning(post: Post) -> bool:
    """Mark the post as read. Only one caller ever wins, so a post is read once."""
    PostBrain.objects.get_or_create(post=post)
    claimed = PostBrain.objects.filter(post=post, learned_at__isnull=True).update(
        learned_at=timezone.now(), updated_at=timezone.now()
    )
    return claimed == 1


def release_post_learning(post: Post) -> None:
    """Undo the claim after a failed read, so the next comment on the post tries again."""
    PostBrain.objects.filter(post=post).update(learned_at=None, updated_at=timezone.now())


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
    source_reply: CommentReply | None = None, source_run: AgentRun | None = None,
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
                source_reply=source_reply, source_run=source_run,
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
    """Send times of everything the agent sent on the account since `since` (public replies and DMs), oldest first."""
    replies = CommentReply.objects.filter(comment__account=account, status=ReplyStatus.SENT, sent_at__gte=since)
    dms = OutgoingDM.objects.filter(run__account=account, status=ReplyStatus.SENT, sent_at__gte=since)
    return sorted([*replies.values_list("sent_at", flat=True), *dms.values_list("sent_at", flat=True)])


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


def get_comment_reply(comment: Comment) -> CommentReply | None:
    return CommentReply.objects.filter(comment=comment).first()


def overdue_queued_reply_ids(before: datetime) -> list[int]:
    return list(
        CommentReply.objects.filter(status=ReplyStatus.QUEUED, scheduled_for__lt=before).values_list("pk", flat=True)
    )


# Agent runs

CLAIMABLE_RUN_STATUSES = (RunStatus.QUEUED, RunStatus.DONE, RunStatus.WAITING_FOLLOWUP, RunStatus.WAITING_SELLER)


def run_exists(comment: Comment) -> bool:
    return AgentRun.objects.filter(comment=comment).exists()


def create_run_if_new(comment: Comment, **fields) -> tuple[AgentRun, bool]:
    return AgentRun.objects.get_or_create(comment=comment, defaults={"account": comment.account, **fields})


def create_dm_run(account: SocialAccount, **fields) -> AgentRun:
    return AgentRun.objects.create(account=account, **fields)


def get_run(run_id: int) -> AgentRun | None:
    return AgentRun.objects.select_related("account", "comment", "conversation").filter(pk=run_id).first()


def claim_run(run_id: int, *, stale_before: datetime) -> str | None:
    """Move a run to running. Only one worker can win. A run left "running" since before `stale_before` (its worker
    died) can be taken over. Returns the status it had, or None when another worker holds it or it is finished."""
    row = (
        AgentRun.objects.filter(pk=run_id)
        .filter(Q(status__in=CLAIMABLE_RUN_STATUSES) | Q(status=RunStatus.RUNNING, updated_at__lt=stale_before))
        .values("status", "updated_at")
        .first()
    )
    if row is None:
        return None
    claimed = AgentRun.objects.filter(pk=run_id, status=row["status"], updated_at=row["updated_at"]).update(
        status=RunStatus.RUNNING, updated_at=timezone.now()
    )
    return row["status"] if claimed == 1 else None


def update_run(run: AgentRun, **fields) -> AgentRun:
    for name, value in fields.items():
        setattr(run, name, value)
    run.save(update_fields=[*fields, "updated_at"])
    return run


def latest_run_for_conversation(conversation: Conversation) -> AgentRun | None:
    return AgentRun.objects.filter(conversation=conversation).first()


def latest_run_for_participant(account: SocialAccount, participant_external_id: str) -> AgentRun | None:
    """The newest run that DMed this person but has no conversation yet (the private reply's answer)."""
    return (
        AgentRun.objects.filter(
            account=account, comment__author_external_id=participant_external_id, conversation__isnull=True,
            private_reply_used_at__isnull=False,
        )
        .first()
    )


def comment_runs_since(account: SocialAccount, buyer_external_id: str, since: datetime) -> int:
    return AgentRun.objects.filter(
        account=account, buyer_external_id=buyer_external_id, comment__isnull=False, created_at__gte=since
    ).count()


def due_wake_run_ids(before: datetime) -> list[int]:
    return list(
        AgentRun.objects.filter(wake_at__lt=before).exclude(status__in=FINISHED_RUN_STATUSES).values_list("pk", flat=True)
    )


def runs_for_comments(comment_ids: set[int]) -> dict[int, AgentRun]:
    """Runs by comment id, with their actions."""
    runs = AgentRun.objects.filter(comment_id__in=comment_ids).prefetch_related("actions")
    return {run.comment_id: run for run in runs}


# Events

def add_event(run: AgentRun, kind: str, payload: dict | None = None, available_at: datetime | None = None) -> AgentEvent:
    return AgentEvent.objects.create(run=run, kind=kind, payload=payload or {}, available_at=available_at)


def due_events(run: AgentRun, now: datetime) -> list[AgentEvent]:
    return list(
        AgentEvent.objects.filter(run=run, processed_at__isnull=True)
        .filter(Q(available_at__isnull=True) | Q(available_at__lte=now))
    )


def mark_events_processed(events: list[AgentEvent]) -> None:
    AgentEvent.objects.filter(pk__in=[event.pk for event in events]).update(processed_at=timezone.now())


def pending_followups(run: AgentRun) -> list[AgentEvent]:
    return list(AgentEvent.objects.filter(run=run, kind=EventKind.FOLLOWUP, processed_at__isnull=True))


def drop_pending_events(run: AgentRun) -> int:
    """Mark every event not handled yet as done, so a finished run never wakes again."""
    return AgentEvent.objects.filter(run=run, processed_at__isnull=True).update(processed_at=timezone.now())


def delay_pending_dms(run: AgentRun, available_at: datetime) -> None:
    AgentEvent.objects.filter(run=run, kind=EventKind.DM, processed_at__isnull=True).update(available_at=available_at)


def next_event_time(run: AgentRun, now: datetime) -> datetime | None:
    """When the earliest follow-up that is not due yet comes due."""
    event = (
        AgentEvent.objects.filter(run=run, kind=EventKind.FOLLOWUP, processed_at__isnull=True, available_at__gt=now)
        .order_by("available_at")
        .first()
    )
    return event.available_at if event else None


# Actions

def add_action(run: AgentRun, *, tool: str, input: dict, result: str, is_error: bool) -> AgentAction:
    return AgentAction.objects.create(run=run, tool=tool, input=input, result=result, is_error=is_error)


def get_action(action_id: int) -> AgentAction | None:
    return AgentAction.objects.select_related("run__account", "run__comment").filter(pk=action_id).first()


# Seller questions

def create_question(run: AgentRun, *, tool_use_id: str, question: str) -> SellerQuestion:
    return SellerQuestion.objects.create(run=run, tool_use_id=tool_use_id, question=question)


def has_open_question(run: AgentRun) -> bool:
    return SellerQuestion.objects.filter(run=run, status=QuestionStatus.OPEN).exists()


def get_question(question_id) -> SellerQuestion | None:
    return SellerQuestion.objects.filter(pk=question_id).first() if question_id else None


def question_count(run: AgentRun) -> int:
    return SellerQuestion.objects.filter(run=run).count()


def expired_open_questions(asked_before: datetime) -> list[SellerQuestion]:
    return list(
        SellerQuestion.objects.filter(status=QuestionStatus.OPEN, created_at__lt=asked_before).select_related("run")
    )


def get_question_for_user(user, question_id: int) -> SellerQuestion | None:
    return (
        SellerQuestion.objects.select_related("run__account", "run__comment")
        .filter(run__account__user=user, pk=question_id)
        .first()
    )


def update_question(question: SellerQuestion, **fields) -> SellerQuestion:
    for name, value in fields.items():
        setattr(question, name, value)
    question.save(update_fields=[*fields, "updated_at"])
    return question


def open_questions_for_user(user, limit: int) -> list[SellerQuestion]:
    return list(
        SellerQuestion.objects.filter(run__account__user=user, status=QuestionStatus.OPEN)
        .select_related("run__account", "run__comment")
        .prefetch_related("run__actions")
        .order_by("-created_at")[:limit]
    )


def open_question_count(user) -> int:
    return SellerQuestion.objects.filter(run__account__user=user, status=QuestionStatus.OPEN).count()


# Outgoing DMs

def create_dm(run: AgentRun, **fields) -> OutgoingDM:
    return OutgoingDM.objects.create(run=run, **fields)


def get_dm(dm_id: int) -> OutgoingDM | None:
    return OutgoingDM.objects.select_related("run__account", "run__comment", "run__conversation").filter(pk=dm_id).first()


def get_dm_for_user(user, dm_id: int) -> OutgoingDM | None:
    return (
        OutgoingDM.objects.select_related("run__account", "run__comment", "run__conversation")
        .filter(run__account__user=user, pk=dm_id)
        .first()
    )


def update_dm(dm: OutgoingDM, **fields) -> OutgoingDM:
    for name, value in fields.items():
        setattr(dm, name, value)
    dm.save(update_fields=[*fields, "updated_at"])
    return dm


def claim_dm_for_sending(dm_id: int) -> bool:
    """Move a queued DM to sending. Only one caller can win, so a DM is never sent twice."""
    claimed = OutgoingDM.objects.filter(pk=dm_id, status=ReplyStatus.QUEUED).update(
        status=ReplyStatus.SENDING, attempts=F("attempts") + 1, updated_at=timezone.now()
    )
    return claimed == 1


def pending_dms_for_user(user, limit: int) -> list[OutgoingDM]:
    return list(
        OutgoingDM.objects.filter(run__account__user=user, status=ReplyStatus.PENDING_REVIEW)
        .select_related("run__account", "run__comment")
        .prefetch_related("run__actions")
        .order_by("-created_at")[:limit]
    )


def pending_dm_count(user) -> int:
    return OutgoingDM.objects.filter(run__account__user=user, status=ReplyStatus.PENDING_REVIEW).count()


def overdue_queued_dm_ids(before: datetime) -> list[int]:
    return list(OutgoingDM.objects.filter(status=ReplyStatus.QUEUED, scheduled_for__lt=before).values_list("pk", flat=True))


# Support tickets

def create_ticket(**fields) -> SupportTicket:
    return SupportTicket.objects.create(**fields)


def get_ticket(ticket_id: int) -> SupportTicket | None:
    return SupportTicket.objects.select_related("account", "run__comment").filter(pk=ticket_id).first()


def get_ticket_for_user(user, ticket_id: int) -> SupportTicket | None:
    return SupportTicket.objects.filter(account__user=user, pk=ticket_id).first()


def update_ticket(ticket: SupportTicket, **fields) -> SupportTicket:
    for name, value in fields.items():
        setattr(ticket, name, value)
    ticket.save(update_fields=[*fields, "updated_at"])
    return ticket


def open_ticket_for_product(account: SocialAccount, buyer_external_id: str, product: str) -> SupportTicket | None:
    return (
        SupportTicket.objects.filter(account=account, buyer_external_id=buyer_external_id, product__iexact=product)
        .exclude(status=TicketStatus.RESOLVED)
        .first()
    )


def tickets_for_buyer(account: SocialAccount, buyer_external_id: str, limit: int = 10) -> list[SupportTicket]:
    return list(SupportTicket.objects.filter(account=account, buyer_external_id=buyer_external_id)[:limit])


def tickets_for_user(user, status: str, limit: int) -> list[SupportTicket]:
    return list(
        SupportTicket.objects.filter(account__user=user, status=status)
        .select_related("account", "run__comment")
        .order_by("-created_at")[:limit]
    )


def ticket_counts_for_user(user) -> dict[str, int]:
    rows = SupportTicket.objects.filter(account__user=user).values("status").annotate(total=Count("pk"))
    return {row["status"]: row["total"] for row in rows}

"""Reply agent: decides what to do with new comments and sends replies within Meta's limits.

Flow: a webhook comment queues `handle_new_comment`, which asks the engine (`engines/`) for a decision. Spam is
skipped, a reply the brains cannot fully back (low confidence) or that fails a safety check waits for review, and
everything else is scheduled. `schedule_reply` hands out send slots at least MIN_SPACING_SECONDS apart per account, and
`send_reply` re-checks the hourly and daily caps and any rate limit pause right before calling the platform.
"""

import logging
import random
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.agent import model_querysets as queries
from apps.agent.constants import ReplyStatus
from apps.agent.engines.base import AgentContext, AgentDecision
from apps.agent.engines.registry import get_engine
from apps.agent.exceptions import ReplyNotEditable, ReplyNotFound, ReplyTextRejected
from apps.agent.models import AgentProfile, CommentReply, PostBrain
from apps.comments import model_querysets as comment_queries
from apps.comments.models import Comment
from apps.content import model_querysets as post_queries
from apps.content import services as content_services
from apps.content.models import Post
from apps.core.exceptions import HookdError
from apps.integrations import model_querysets as account_queries
from apps.integrations.constants import Capability
from apps.integrations.exceptions import ProviderAPIError, RateLimitedError, TokenExpiredError
from apps.integrations.models import SocialAccount
from apps.integrations.providers.registry import get_provider

logger = logging.getLogger(__name__)

INBOX_PAGE_SIZE = 100
PREVIEW_POST_CHOICES = 20
# A queued reply this far past its slot was probably lost from the Celery queue.
OVERDUE_AFTER = timedelta(minutes=5)
# A task that fires a little early (clock drift) still sends.
EARLY_TOLERANCE = timedelta(seconds=5)
MENTION_PATTERN = re.compile(r"^@\S+\s*")
SPAM_INTENT = "spam"

def _limit(name: str):
    return settings.AGENT_REPLY_LIMITS[name]


# Settings and brains


def agent_accounts(user) -> list[SocialAccount]:
    return list(account_queries.list_accounts_for_user(user))


def get_profile(account: SocialAccount) -> AgentProfile:
    return queries.get_or_create_profile(account)


def update_profile(account: SocialAccount, **fields) -> AgentProfile:
    return queries.update_profile(queries.get_or_create_profile(account), **fields)


def sending_limits() -> dict:
    return settings.AGENT_REPLY_LIMITS


def is_paused(profile: AgentProfile) -> bool:
    return bool(profile.paused_until and profile.paused_until > timezone.now())


def supports_replies(account: SocialAccount) -> bool:
    return Capability.PUBLIC_REPLY in get_provider(account.platform).capabilities


def get_post_brain(post: Post) -> PostBrain | None:
    return queries.get_post_brain(post)


def save_post_brain(post: Post, *, knowledge: str, agent_enabled: bool) -> PostBrain:
    return queries.save_post_brain(post, knowledge=knowledge, agent_enabled=agent_enabled)


def preview_posts(account: SocialAccount) -> list[Post]:
    return post_queries.latest_posts(account, PREVIEW_POST_CHOICES)


def pending_review_count(user) -> int:
    return queries.pending_review_count(user)


# Deciding


@dataclass
class Routing:
    status: str
    reason: str


def _context(*, account: SocialAccount, profile: AgentProfile, post: Post | None, brain: PostBrain | None, text: str, author: str) -> AgentContext:
    return AgentContext(
        comment_text=text,
        author_username=author,
        account_username=account.username,
        voice=profile.voice,
        account_knowledge=profile.knowledge,
        post_caption=post.caption if post else "",
        post_knowledge=brain.knowledge if brain else "",
    )


def _route(*, account: SocialAccount, decision: AgentDecision, check_repeats: bool) -> Routing:
    """Send when the engine is sure the brains answer the comment; otherwise, or when a safety check fails, review."""
    if decision.intent.lower() == SPAM_INTENT:
        return Routing(ReplyStatus.SKIPPED, "Spam, not answered.")
    problems = get_provider(account.platform).reply_text_problems(decision.reply_text)
    if problems:
        return Routing(ReplyStatus.PENDING_REVIEW, " ".join(problems))
    if decision.confidence < _limit("AUTO_REPLY_MIN_CONFIDENCE"):
        return Routing(ReplyStatus.PENDING_REVIEW, f"Missing information: {decision.reason or 'the agent is not sure.'}")
    if check_repeats and _same_text_count(account, decision.reply_text) >= _limit("MAX_SAME_TEXT_PER_HOUR"):
        return Routing(ReplyStatus.PENDING_REVIEW, "The same reply already went out several times this hour.")
    return Routing(ReplyStatus.QUEUED, "The brains have the answer, sent automatically.")


def _normalized(text: str) -> str:
    return MENTION_PATTERN.sub("", text).strip().lower()


def _same_text_count(account: SocialAccount, text: str) -> int:
    target = _normalized(text)
    hour_ago = timezone.now() - timedelta(hours=1)
    return sum(1 for sent in queries.outgoing_texts_since(account, hour_ago) if _normalized(sent) == target)


def _decision_record(decision: AgentDecision, engine_name: str, reason: str) -> dict:
    return {
        "intent": decision.intent,
        "language": decision.language,
        "confidence": decision.confidence,
        "engine": engine_name,
        "engine_reason": decision.reason,
        "reason": reason,
    }


def _skip_reason(comment: Comment, profile: AgentProfile, brain: PostBrain | None) -> str:
    if brain is not None and not brain.agent_enabled:
        return "The agent is off for this post."
    if comment.parent_external_id and not profile.reply_to_threads:
        return "Reply inside a thread."
    max_age = timedelta(hours=_limit("MAX_COMMENT_AGE_HOURS"))
    if comment.commented_at and comment.commented_at < timezone.now() - max_age:
        return "The comment is too old to answer automatically."
    return ""


def handle_new_comment(comment_id: int) -> str:
    """Decide what to do with a comment that just arrived by webhook. Returns the outcome (stored as task result)."""
    comment = comment_queries.get_comment(comment_id)
    if comment is None:
        return "missing"
    account = comment.account
    if comment.is_from_account:
        return "ignored"
    # Stores posts made outside Hookd even when the agent is off, so the studio knows about them.
    post = content_services.ensure_post(account, comment.media_external_id)
    profile = queries.get_profile(account)
    if profile is None or not profile.enabled or not supports_replies(account):
        return "ignored"
    if queries.reply_exists(comment):
        return "exists"

    brain = queries.get_post_brain(post) if post else None
    skip = _skip_reason(comment, profile, brain)
    if skip:
        queries.create_reply_if_new(comment, status=ReplyStatus.SKIPPED, decision={"reason": skip})
        return ReplyStatus.SKIPPED

    engine = get_engine()
    context = _context(
        account=account, profile=profile, post=post, brain=brain, text=comment.text, author=comment.author_username
    )
    try:
        decision = engine.decide(context)
    except HookdError as exc:
        # The AI could not answer: the creator writes this one by hand in the inbox.
        logger.warning("Reply engine failed for comment %s: %s", comment.pk, exc.message)
        queries.create_reply_if_new(
            comment,
            status=ReplyStatus.PENDING_REVIEW,
            decision={"engine": engine.name, "reason": f"The AI could not write a reply: {exc.message}"},
        )
        return ReplyStatus.PENDING_REVIEW
    routing = _route(account=account, decision=decision, check_repeats=True)
    reply, created = queries.create_reply_if_new(
        comment,
        status=ReplyStatus.PENDING_REVIEW if routing.status == ReplyStatus.QUEUED else routing.status,
        draft_text=decision.reply_text,
        final_text=decision.reply_text,
        decision=_decision_record(decision, engine.name, routing.reason),
    )
    if not created:
        return "exists"
    if routing.status == ReplyStatus.QUEUED:
        schedule_reply(reply)
    return routing.status


# Preview


@dataclass
class Preview:
    decision: AgentDecision
    outcome: str
    reason: str


def preview_reply(*, account: SocialAccount, text: str, post: Post | None = None) -> Preview:
    """What the agent would do with `text`. Stores and sends nothing."""
    profile = queries.get_or_create_profile(account)
    brain = queries.get_post_brain(post) if post else None
    context = _context(account=account, profile=profile, post=post, brain=brain, text=text, author="customer")
    decision = get_engine().decide(context)
    if brain is not None and not brain.agent_enabled:
        routing = Routing(ReplyStatus.SKIPPED, "The agent is off for this post.")
    else:
        routing = _route(account=account, decision=decision, check_repeats=False)
    return Preview(decision=decision, outcome=routing.status, reason=routing.reason)


# Sending


def schedule_reply(reply: CommentReply, *, not_before: datetime | None = None) -> CommentReply:
    """Queue a reply in the account's next free send slot and hand it to Celery once the transaction commits."""
    from apps.agent.tasks import send_reply as send_reply_task

    account = reply.comment.account
    with transaction.atomic():
        profile = queries.lock_profile(account)
        now = timezone.now()
        slot = max(moment for moment in (now, profile.next_send_at, profile.paused_until, not_before) if moment)
        spacing = timedelta(seconds=_limit("MIN_SPACING_SECONDS") + random.uniform(0, _limit("JITTER_SECONDS")))
        queries.update_profile(profile, next_send_at=slot + spacing)
        queries.update_reply(reply, status=ReplyStatus.QUEUED, scheduled_for=slot, error="")
        transaction.on_commit(lambda: send_reply_task.apply_async(args=[reply.pk], eta=slot))
    return reply


def _cap_frees_at(account: SocialAccount, now: datetime) -> datetime | None:
    """When the hourly or daily cap is reached, the moment a slot frees up again. None when under both caps."""
    frees_at = None
    for window, cap in ((timedelta(hours=1), _limit("MAX_PER_HOUR")), (timedelta(days=1), _limit("MAX_PER_DAY"))):
        sent = queries.sent_times_since(account, now - window)
        if len(sent) >= cap:
            candidate = sent[len(sent) - cap] + window
            frees_at = max(frees_at, candidate) if frees_at else candidate
    return frees_at


def send_reply(reply_id: int) -> str:
    """Send one queued reply if its slot has come. Returns the outcome (stored as task result)."""
    reply = queries.get_reply(reply_id)
    if reply is None:
        return "missing"
    now = timezone.now()
    if reply.status != ReplyStatus.QUEUED:
        return "not_queued"
    if reply.scheduled_for and reply.scheduled_for > now + EARLY_TOLERANCE:
        return "not_due"
    if not queries.claim_reply_for_sending(reply.pk):
        return "not_queued"
    reply.refresh_from_db()

    account = reply.comment.account
    if account.status != SocialAccount.Status.ACTIVE:
        queries.update_reply(reply, status=ReplyStatus.FAILED, error="The account needs attention on the connections page.")
        return ReplyStatus.FAILED

    profile = queries.get_or_create_profile(account)
    if is_paused(profile):
        schedule_reply(reply, not_before=profile.paused_until)
        return "paused"
    frees_at = _cap_frees_at(account, now)
    if frees_at:
        schedule_reply(reply, not_before=frees_at)
        return "capped"

    provider = get_provider(account.platform)
    problems = provider.reply_text_problems(reply.final_text)
    if problems:
        queries.update_reply(reply, status=ReplyStatus.PENDING_REVIEW, error=" ".join(problems))
        return ReplyStatus.PENDING_REVIEW

    try:
        external_id = provider.reply_to_comment(account, reply.comment.external_id, reply.final_text)
    except RateLimitedError as exc:
        pause_until = timezone.now() + timedelta(minutes=_limit("RATE_LIMIT_PAUSE_MINUTES"))
        queries.update_profile(profile, paused_until=pause_until, pause_reason=exc.message)
        logger.warning("Pausing replies for account %s until %s: %s", account.pk, pause_until, exc.message)
        schedule_reply(reply, not_before=pause_until)
        return "rate_limited"
    except TokenExpiredError as exc:
        account_queries.set_account_status(account, SocialAccount.Status.EXPIRED, exc.message)
        queries.update_reply(reply, status=ReplyStatus.FAILED, error=exc.message)
        return ReplyStatus.FAILED
    except ProviderAPIError as exc:
        queries.update_reply(reply, status=ReplyStatus.FAILED, error=exc.message)
        return ReplyStatus.FAILED

    queries.update_reply(
        reply, status=ReplyStatus.SENT, sent_at=timezone.now(), reply_external_id=external_id, error=""
    )
    return ReplyStatus.SENT


def send_overdue_replies() -> int:
    """Recovery for replies whose Celery task was lost (for example a wiped Redis). Returns how many were sent."""
    sent = 0
    for reply_id in queries.overdue_queued_reply_ids(timezone.now() - OVERDUE_AFTER):
        sent += int(send_reply(reply_id) == ReplyStatus.SENT)
    return sent


# Inbox


def inbox(user, status: str) -> list[CommentReply]:
    return queries.list_replies_for_user(user, status, INBOX_PAGE_SIZE)


def inbox_counts(user) -> dict[str, int]:
    return queries.reply_counts_for_user(user)


def get_user_reply(*, user, reply_id: int) -> CommentReply:
    reply = queries.get_reply_for_user(user, reply_id)
    if reply is None:
        raise ReplyNotFound()
    return reply


def approve_reply(reply: CommentReply, text: str) -> CommentReply:
    if reply.status != ReplyStatus.PENDING_REVIEW:
        raise ReplyNotEditable()
    text = text.strip()
    problems = get_provider(reply.comment.account.platform).reply_text_problems(text)
    if problems:
        raise ReplyTextRejected(" ".join(problems))
    queries.update_reply(reply, final_text=text)
    return schedule_reply(reply)


def reject_reply(reply: CommentReply) -> CommentReply:
    if reply.status != ReplyStatus.PENDING_REVIEW:
        raise ReplyNotEditable()
    return queries.update_reply(reply, status=ReplyStatus.REJECTED)


def retry_reply(reply: CommentReply) -> CommentReply:
    if reply.status != ReplyStatus.FAILED:
        raise ReplyNotEditable()
    return schedule_reply(reply)

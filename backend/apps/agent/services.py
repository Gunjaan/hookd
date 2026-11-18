"""Reply agent: decides what to do with new comments and sends replies within Meta's limits.

Flow: a webhook comment queues `handle_new_comment`, which asks the engine (`engines/`) for a decision. Spam is
skipped, a reply the brains cannot fully back (low confidence) or that fails a safety check waits for review, and
everything else is scheduled. `schedule_reply` hands out send slots at least MIN_SPACING_SECONDS apart per account, and
`send_reply` re-checks the hourly and daily caps and any rate limit pause right before calling the platform.

The brains also fill themselves: a post's caption is read once (the first comment on it, or when Hookd publishes it),
and every inbox reply the creator rewrote is read too. The brain learner (`learn_from_post`, `learn_from_reply`) turns
them into LearnedFact rows that sit next to what the creator typed. What the creator typed wins on the same topic.
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
from apps.agent.constants import FactScope, FactSource, ReplyStatus
from apps.agent.engines.base import AgentContext, AgentDecision, LearnedFactDraft
from apps.agent.engines.registry import get_engine, get_learner
from apps.agent.exceptions import LearnedFactNotFound, ReplyNotEditable, ReplyNotFound, ReplyTextRejected
from apps.agent.models import AgentProfile, CommentReply, LearnedFact, PostBrain
from apps.ai.constants import Purpose
from apps.ai.services import attributed
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
# Learned facts per brain (shop, post) handed to the engine, newest first.
LEARNED_FACTS_LIMIT = 40

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


def sent_reply_count(account: SocialAccount, days: int | None = None) -> int:
    """Replies the agent has posted on the account, all time (studio profile) or in the last `days` (agent page)."""
    since = timezone.now() - timedelta(days=days) if days else None
    return queries.sent_reply_count(account, since)


# Deciding


@dataclass
class Routing:
    status: str
    reason: str


def _brain_text(written: str, facts: list[LearnedFact]) -> str:
    """The creator's lines, then learned facts on topics the creator did not write about: what they typed wins."""
    written_labels = {line.split(":", 1)[0].strip().lower() for line in written.splitlines() if ":" in line}
    learned = [fact.line for fact in facts if fact.label.lower() not in written_labels]
    return "\n".join(part for part in (written.strip(), *learned) if part)


def _context(
    *, account: SocialAccount, profile: AgentProfile, post: Post | None, brain: PostBrain | None, text: str, author: str,
    voice: str | None = None, knowledge: str | None = None,
) -> AgentContext:
    """`voice` and `knowledge` replace the saved ones (the agent page tests unsaved edits)."""
    facts = queries.learned_facts(account, post, LEARNED_FACTS_LIMIT)
    return AgentContext(
        comment_text=text,
        author_username=author,
        account_username=account.username,
        voice=profile.voice if voice is None else voice,
        account_knowledge=_brain_text(
            profile.knowledge if knowledge is None else knowledge, [fact for fact in facts if fact.post_id is None]
        ),
        post_caption=post.caption if post else "",
        post_knowledge=_brain_text(brain.knowledge if brain else "", [fact for fact in facts if fact.post_id]),
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
    if post is not None:
        # Read the caption the first time someone comments on the post, before answering, so this reply can use it.
        learn_from_post(post.pk)
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
        with attributed(Purpose.COMMENT_REPLY, account=account, subject=comment):
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


def preview_reply(
    *, account: SocialAccount, text: str, post: Post | None = None, voice: str | None = None, knowledge: str | None = None
) -> Preview:
    """What the agent would do with `text`. `voice` and `knowledge` replace the saved ones (unsaved edits). Stores and sends nothing."""
    profile = queries.get_or_create_profile(account)
    brain = queries.get_post_brain(post) if post else None
    context = _context(
        account=account, profile=profile, post=post, brain=brain, text=text, author="customer", voice=voice,
        knowledge=knowledge,
    )
    with attributed(Purpose.REPLY_PREVIEW, account=account, subject=post):
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
    """Newest replies with this status. Each gets `post`: the stored post its comment is on, or None."""
    replies = queries.list_replies_for_user(user, status, INBOX_PAGE_SIZE)
    posts = post_queries.posts_for_user_by_external_id(
        user, {reply.comment.media_external_id for reply in replies if reply.comment.media_external_id}
    )
    for reply in replies:
        reply.post = posts.get((reply.comment.account_id, reply.comment.media_external_id))
    return replies


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
    if reply.written_by_creator:
        # The creator wrote what Hooky did not know: learn it for the next buyer asking the same.
        from apps.agent.tasks import learn_from_reply as learn_from_reply_task

        transaction.on_commit(lambda: learn_from_reply_task.delay(reply.pk))
    return schedule_reply(reply)


def reject_reply(reply: CommentReply) -> CommentReply:
    if reply.status != ReplyStatus.PENDING_REVIEW:
        raise ReplyNotEditable()
    return queries.update_reply(reply, status=ReplyStatus.REJECTED)


def retry_reply(reply: CommentReply) -> CommentReply:
    if reply.status != ReplyStatus.FAILED:
        raise ReplyNotEditable()
    return schedule_reply(reply)


# Learning


def _placed(facts: list[LearnedFactDraft], post: Post | None) -> list[tuple[Post | None, str, str]]:
    """(post, label, text) rows: post facts on the post, shop facts (or any fact without a post) on the account."""
    return [(post if fact.scope == FactScope.POST else None, fact.label, fact.text) for fact in facts]


def learn_from_post(post_id: int) -> str:
    """Learn the facts in a post's caption, once per post. Returns the outcome (stored as task result)."""
    post = post_queries.get_post(post_id)
    if post is None:
        return "missing"
    if not queries.claim_caption_learning(post):
        return "already_learned"
    if not post.caption.strip():
        return "learned 0"
    try:
        with attributed(Purpose.LEARN_CAPTION, account=post.account, subject=post):
            facts = get_learner().from_caption(post.caption)
    except HookdError as exc:
        queries.release_caption_learning(post)
        logger.warning("Could not learn from the caption of post %s: %s", post.pk, exc.message)
        return "failed"
    queries.save_learned_facts(post.account, _placed(facts, post), source=FactSource.CAPTION, source_post=post)
    return f"learned {len(facts)}"


def learn_from_reply(reply_id: int) -> str:
    """Learn the facts in a reply the creator wrote in the inbox. Returns the outcome (stored as task result)."""
    reply = queries.get_reply(reply_id)
    if reply is None:
        return "missing"
    comment = reply.comment
    post = post_queries.find_post(comment.account, comment.media_external_id)
    try:
        with attributed(Purpose.LEARN_REPLY, account=comment.account, subject=reply):
            facts = get_learner().from_reply(
                question=comment.text, answer=reply.final_text, post_caption=post.caption if post else ""
            )
    except HookdError as exc:
        logger.warning("Could not learn from reply %s: %s", reply.pk, exc.message)
        return "failed"
    queries.save_learned_facts(
        comment.account, _placed(facts, post), source=FactSource.REPLY, source_post=post, source_reply=reply
    )
    return f"learned {len(facts)}"


def account_learned_facts(account: SocialAccount) -> list[LearnedFact]:
    return queries.account_learned_facts(account, LEARNED_FACTS_LIMIT)


def post_learned_facts(post: Post) -> list[LearnedFact]:
    return queries.post_learned_facts(post, LEARNED_FACTS_LIMIT)


def post_learned_fact_count(account: SocialAccount) -> int:
    return queries.post_learned_fact_count(account)


def forget_fact(*, user, fact_id: int) -> None:
    fact = queries.get_learned_fact_for_user(user, fact_id)
    if fact is None:
        raise LearnedFactNotFound()
    queries.delete_learned_fact(fact)

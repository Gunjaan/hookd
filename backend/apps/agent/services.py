"""Reply agent: runs an agent loop for every new comment and buyer DM, and sends what it writes within Meta's limits.

Flow: a webhook comment or DM starts (or continues) an AgentRun and queues `advance_run`. The loop (`loop.py`) lets
the model use the tools (`tools.py`): look facts up, reply publicly, DM, ask the seller, learn, open a ticket, come
back later. Whatever it sends goes through the outboxes here: public replies (CommentReply) and DMs (OutgoingDM)
share one clock per account, so `schedule_*` hands out slots at least MIN_SPACING_SECONDS apart and `send_*` re-checks
the hourly and daily caps, rate limit pauses and Meta's messaging rules right before calling the platform.

The brains also fill themselves: a post's caption is read once (the first comment on it, or when Hookd publishes it),
every inbox reply the creator rewrote is read too, and the agent saves what the seller tells it (save_fact). Learned
facts sit next to what the creator typed; what the creator typed wins on the same topic.
"""

import logging
import random
import re
from datetime import datetime, timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone

from apps.agent import model_querysets as queries
from apps.agent.constants import (
    DMKind,
    EventKind,
    FactScope,
    FactSource,
    QuestionStatus,
    ReplyStatus,
    RunStatus,
)
from apps.agent.engines.base import LearnedFactDraft
from apps.agent.engines.registry import get_learner
from apps.agent.exceptions import (
    LearnedFactNotFound,
    QuestionNotFound,
    QuestionNotOpen,
    ReplyNotEditable,
    ReplyNotFound,
    ReplyTextRejected,
    TicketNotFound,
)
from apps.agent.models import (
    AgentProfile,
    AgentRun,
    CommentReply,
    LearnedFact,
    OutgoingDM,
    PostBrain,
    SellerQuestion,
    SupportTicket,
)
from apps.ai.constants import Purpose
from apps.ai.services import attributed
from apps.comments import model_querysets as comment_queries
from apps.comments.models import Comment
from apps.content import model_querysets as post_queries
from apps.content import services as content_services
from apps.content.models import Post
from apps.core.exceptions import HookdError
from apps.integrations import model_querysets as account_queries
from apps.integrations.constants import Capability, MessageKind
from apps.integrations.exceptions import ProviderAPIError, RateLimitedError, TokenExpiredError
from apps.integrations.models import SocialAccount
from apps.integrations.providers.registry import get_provider
from apps.messaging import model_querysets as message_queries
from apps.messaging.models import Conversation, DirectMessage

logger = logging.getLogger(__name__)

INBOX_PAGE_SIZE = 100
PREVIEW_POST_CHOICES = 20
# A queued message or a follow-up this far past its time was probably lost from the Celery queue.
OVERDUE_AFTER = timedelta(minutes=5)
# A task that fires a little early (clock drift) still sends.
EARLY_TOLERANCE = timedelta(seconds=5)
# A DM is only sent while the 24 hour window has at least this long left.
WINDOW_MARGIN = timedelta(minutes=5)
# A buyer DM continues their latest run while it is this recent; after that a fresh run starts.
RUN_CONTINUES_FOR = timedelta(days=7)
MENTION_PATTERN = re.compile(r"^@\S+\s*")
# Learned facts per brain (shop, post) handed to the agent, newest first.
LEARNED_FACTS_LIMIT = 40
# How much of a shared post's caption the DM shows; get_post reads the whole caption.
SHARED_CAPTION_PREVIEW = 300


def reply_limit(name: str):
    return settings.AGENT_REPLY_LIMITS[name]


def _loop_limit(name: str):
    return settings.AGENT_LOOP_LIMITS[name]


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


def attention_count(user) -> int:
    """What waits for the seller: replies and DMs to review, and Hooky's open questions (sidebar badge)."""
    return queries.pending_review_count(user) + queries.pending_dm_count(user) + queries.open_question_count(user)


def sent_reply_count(account: SocialAccount, days: int | None = None) -> int:
    """Replies the agent has posted on the account, all time (studio profile) or in the last `days` (agent page)."""
    since = timezone.now() - timedelta(days=days) if days else None
    return queries.sent_reply_count(account, since)


def _brain_text(written: str, facts: list[LearnedFact]) -> str:
    """The creator's lines, then learned facts on topics the creator did not write about: what they typed wins."""
    written_labels = {line.split(":", 1)[0].strip().lower() for line in written.splitlines() if ":" in line}
    learned = [fact.line for fact in facts if fact.label.lower() not in written_labels]
    return "\n".join(part for part in (written.strip(), *learned) if part)


def brain_parts(
    account: SocialAccount, profile: AgentProfile, post: Post | None, *, knowledge: str | None = None
) -> tuple[str, str]:
    """(shop brain, post brain) as text. `knowledge` replaces the saved account brain (the agent page tests unsaved
    edits)."""
    facts = queries.learned_facts(account, post, LEARNED_FACTS_LIMIT)
    shop = _brain_text(profile.knowledge if knowledge is None else knowledge, [fact for fact in facts if fact.post_id is None])
    if post is None:
        return shop, ""
    brain = queries.get_post_brain(post)
    return shop, _brain_text(brain.knowledge if brain else "", [fact for fact in facts if fact.post_id])


def post_brain_text(post: Post) -> str:
    brain = queries.get_post_brain(post)
    return _brain_text(brain.knowledge if brain else "", queries.post_learned_facts(post, LEARNED_FACTS_LIMIT))


def _normalized(text: str) -> str:
    return MENTION_PATTERN.sub("", text).strip().lower()


def same_text_count(account: SocialAccount, text: str) -> int:
    target = _normalized(text)
    hour_ago = timezone.now() - timedelta(hours=1)
    return sum(1 for sent in queries.outgoing_texts_since(account, hour_ago) if _normalized(sent) == target)


# Starting and waking runs


def _skip_reason(comment: Comment, profile: AgentProfile, brain: PostBrain | None) -> str:
    if brain is not None and not brain.agent_enabled:
        return "The agent is off for this post."
    if comment.parent_external_id and not profile.reply_to_threads:
        return "Reply inside a thread."
    max_age = timedelta(hours=reply_limit("MAX_COMMENT_AGE_HOURS"))
    if comment.commented_at and comment.commented_at < timezone.now() - max_age:
        return "The comment is too old to answer automatically."
    cap = _loop_limit("MAX_RUNS_PER_BUYER_PER_DAY")
    if comment.author_external_id and queries.comment_runs_since(
        comment.account, comment.author_external_id, timezone.now() - timedelta(days=1)
    ) >= cap:
        return f"Hooky already handled {cap} comments from this person today."
    return ""


def _queue_advance(run: AgentRun, *, eta: datetime | None = None) -> None:
    from apps.agent.tasks import advance_run as advance_run_task

    transaction.on_commit(lambda: advance_run_task.apply_async(args=[run.pk], eta=eta))


def handle_new_comment(comment_id: int) -> str:
    """Start an agent run for a comment that just arrived by webhook. Returns the outcome (stored as task result)."""
    comment = comment_queries.get_comment(comment_id)
    if comment is None:
        return "missing"
    account = comment.account
    if comment.is_from_account:
        return "ignored"
    # Stores posts made outside Hookd even when the agent is off, so the studio knows about them.
    post = content_services.ensure_post(account, comment.media_external_id)
    if post is not None:
        # Read the caption the first time someone comments on the post, before answering, so this run can use it.
        learn_from_post(post.pk)
    profile = queries.get_profile(account)
    if profile is None or not profile.enabled or not supports_replies(account):
        return "ignored"
    if queries.reply_exists(comment) or queries.run_exists(comment):
        return "exists"

    brain = queries.get_post_brain(post) if post else None
    skip = _skip_reason(comment, profile, brain)
    if skip:
        queries.create_reply_if_new(comment, status=ReplyStatus.SKIPPED, decision={"reason": skip})
        return ReplyStatus.SKIPPED

    with transaction.atomic():
        run, created = queries.create_run_if_new(
            comment, buyer_external_id=comment.author_external_id, buyer_username=comment.author_username
        )
        if not created:
            return "exists"
        queries.add_event(run, EventKind.COMMENT)
        _queue_advance(run)
    return "started"


def handle_buyer_message(conversation_id: int, message_id: int) -> str:
    """A buyer wrote to the account: continue their recent run, or start one. A shared post of the seller's that
    Hookd has not seen is fetched first, so the run can read it. Returns the outcome."""
    conversation = message_queries.get_conversation(conversation_id)
    message = message_queries.get_message(message_id)
    if conversation is None or message is None or message.is_from_account:
        return "missing"
    account = conversation.account
    profile = queries.get_profile(account)
    if profile is None or not profile.enabled or Capability.DIRECT_MESSAGE not in get_provider(account.platform).capabilities:
        return "ignored"
    if message.shared_post_external_id:
        content_services.ensure_post(account, message.shared_post_external_id)
    with transaction.atomic():
        run = _run_for_buyer(account, conversation)
        if run is None:
            return "handed_off"
        cancel_followups(run, "Dropped the follow-up: the buyer wrote back.")
        queries.add_event(run, EventKind.DM, {"message_id": message.pk})
        _queue_advance(run)
    return "queued"


def _run_for_buyer(account: SocialAccount, conversation: Conversation) -> AgentRun | None:
    """The buyer's recent run (linked to this thread, or the one whose private reply they are answering), else a
    new run started by this DM. None when Hooky recently handed this buyer to the seller: the seller answers them."""
    since = timezone.now() - RUN_CONTINUES_FOR
    run = queries.latest_run_for_conversation(conversation)
    if run is None:
        run = queries.latest_run_for_participant(account, conversation.participant_external_id)
        if run is not None:
            queries.update_run(run, conversation=conversation)
    if run is not None and run.updated_at >= since:
        if run.status == RunStatus.HANDED_OFF:
            return None
        if run.status != RunStatus.FAILED:
            return run
    known = comment_queries.latest_comment_by_author(account, conversation.participant_external_id)
    return queries.create_dm_run(
        account,
        conversation=conversation,
        buyer_external_id=conversation.participant_external_id,
        buyer_username=known.author_username if known else "",
    )


def cancel_followups(run: AgentRun, reason: str) -> int:
    """Close the run's pending follow-ups (shown in its trail). Returns how many were dropped."""
    followups = queries.pending_followups(run)
    if followups:
        queries.mark_events_processed(followups)
        for event in followups:
            queries.add_action(run, tool="drop_followup", input={"note": event.payload.get("note", "")}, result=reason, is_error=False)
        queries.update_run(run, wake_at=None)
    return len(followups)


def advance_run_later(run: AgentRun) -> None:
    _queue_advance(run)


def advance_run(run_id: int) -> str:
    """Let the agent handle everything new on a run. Returns the outcome (stored as task result)."""
    from apps.agent import loop

    return loop.advance(run_id)


def note_run_finished(run: AgentRun) -> None:
    """A comment the agent chose not to answer publicly still shows in the inbox (Skipped), with what it did."""
    if run.comment_id is None:
        return
    reason = run.outcome or "Hooky did not reply publicly."
    reply = queries.get_comment_reply(run.comment)
    if reply is None:
        queries.create_reply_if_new(run.comment, status=ReplyStatus.SKIPPED, decision={"reason": reason})
    elif reply.status == ReplyStatus.SKIPPED:
        queries.update_reply(reply, decision={**reply.decision, "reason": reason})


def note_run_failed(run: AgentRun, message: str) -> None:
    """The agent could not finish: the creator answers the comment by hand in the inbox."""
    if run.comment_id is None:
        return
    reason = f"Hooky could not finish: {message}"
    reply = queries.get_comment_reply(run.comment)
    if reply is None:
        queries.create_reply_if_new(run.comment, status=ReplyStatus.PENDING_REVIEW, decision={"reason": reason})
    elif reply.status == ReplyStatus.SKIPPED:
        queries.update_reply(reply, status=ReplyStatus.PENDING_REVIEW, decision={**reply.decision, "reason": reason})


def expire_questions() -> int:
    """Questions the seller left open for QUESTION_EXPIRES_HOURS close, so their runs stop waiting and wrap up.
    Returns how many expired."""
    asked_before = timezone.now() - timedelta(hours=_loop_limit("QUESTION_EXPIRES_HOURS"))
    questions = queries.expired_open_questions(asked_before)
    for question in questions:
        _close_question(question, QuestionStatus.EXPIRED, "")
    return len(questions)


def wake_overdue_runs() -> int:
    """Recovery for follow-ups whose Celery task was lost. Returns how many runs were woken."""
    run_ids = queries.due_wake_run_ids(timezone.now() - OVERDUE_AFTER)
    for run_id in run_ids:
        advance_run(run_id)
    return len(run_ids)


# What the tools do


def agent_reply(run: AgentRun, existing: CommentReply | None, text: str, *, approve: bool) -> str:
    """Store the agent's public reply and schedule it, or hold it for approval. Returns the tool result."""
    decision = {"engine": "agent", "reason": "Written by Hooky.", "approval": approve}
    fields = {"draft_text": text, "final_text": text, "decision": decision, "status": ReplyStatus.PENDING_REVIEW}
    if existing is None:
        reply, _ = queries.create_reply_if_new(run.comment, **fields)
    else:
        reply = queries.update_reply(existing, **fields)
    if approve:
        return "Waiting for the seller's approval before it is posted."
    reply = schedule_reply(reply)
    return f"Reply scheduled, goes out around {reply.scheduled_for:%H:%M} UTC."


def dm_route(run: AgentRun, now: datetime) -> tuple[str | None, str]:
    """How a DM to this buyer may go out now: (DMKind, "") or (None, why not). Meta allows a DM within 24 hours of
    the buyer's last message, and one private reply to their comment within 7 days of it."""
    conversation = run.conversation
    if conversation is not None and conversation.reply_window_ends_at and conversation.reply_window_ends_at > now + WINDOW_MARGIN:
        return DMKind.DM, ""
    if run.comment is None:
        return None, "the buyer has not written in the last 24 hours."
    if run.private_reply_used_at:
        return None, "the one private reply to this comment was already used and the buyer has not written back in the last 24 hours."
    commented_at = run.comment.commented_at or run.created_at
    if now > commented_at + timedelta(hours=_loop_limit("PRIVATE_REPLY_MAX_HOURS")):
        return None, "the comment is older than 7 days."
    return DMKind.PRIVATE_REPLY, ""


def dm_allowed_until(run: AgentRun, now: datetime) -> datetime | None:
    """The last moment a DM to this buyer is still allowed (for follow-ups), or None when it no longer is."""
    candidates = []
    conversation = run.conversation
    if conversation is not None and conversation.reply_window_ends_at and conversation.reply_window_ends_at > now:
        candidates.append(conversation.reply_window_ends_at - WINDOW_MARGIN)
    if run.comment is not None and not run.private_reply_used_at:
        commented_at = run.comment.commented_at or run.created_at
        candidates.append(commented_at + timedelta(hours=_loop_limit("PRIVATE_REPLY_MAX_HOURS")))
    latest = max(candidates, default=None)
    return latest if latest and latest > now else None


def agent_dm(run: AgentRun, kind: str, text: str, *, approve: bool) -> str:
    """Store the agent's DM and schedule it, or hold it for approval. Returns the tool result."""
    if kind == DMKind.PRIVATE_REPLY:
        # Taken now, so a second private reply is refused even before the first one is sent.
        queries.update_run(run, private_reply_used_at=timezone.now())
    dm = queries.create_dm(run, kind=kind, text=text, status=ReplyStatus.PENDING_REVIEW)
    if approve:
        return "Waiting for the seller's approval before it is sent."
    dm = schedule_dm(dm)
    return f"DM scheduled, goes out around {dm.scheduled_for:%H:%M} UTC."


def schedule_followup(run: AgentRun, due: datetime, note: str) -> None:
    queries.add_event(run, EventKind.FOLLOWUP, {"note": note}, available_at=due)
    queries.update_run(run, followups_used=run.followups_used + 1, wake_at=due)
    _queue_advance(run, eta=due)


def create_ticket(run: AgentRun, *, product: str, summary: str) -> SupportTicket:
    ticket = queries.create_ticket(
        account=run.account, run=run, buyer_external_id=run.buyer_external_id, buyer_username=run.buyer_username,
        product=product, summary=summary,
    )
    from apps.agent.tasks import email_ticket as email_ticket_task

    transaction.on_commit(lambda: email_ticket_task.delay(ticket.pk))
    return ticket


def buyer_tickets(run: AgentRun) -> list[SupportTicket]:
    if not run.buyer_external_id:
        return []
    return queries.tickets_for_buyer(run.account, run.buyer_external_id)


def email_ticket(ticket_id: int) -> str:
    """Email a ticket to the support address. Returns the outcome (stored as task result)."""
    ticket = queries.get_ticket(ticket_id)
    if ticket is None:
        return "missing"
    if ticket.emailed_at:
        return "already_sent"
    account = ticket.account
    buyer = f"@{ticket.buyer_username}" if ticket.buyer_username else ticket.buyer_external_id or "unknown"
    comment = ticket.run.comment if ticket.run and ticket.run.comment_id else None
    lines = [
        f"Account: @{account.username} ({account.get_platform_display()})",
        f"Buyer: {buyer}",
        f"Product: {ticket.product or 'not given'}",
        f"Problem: {ticket.summary}",
    ]
    if comment is not None:
        lines.append(f"Their comment: {comment.text}")
    lines.append(f"Ticket #{ticket.pk}, opened by Hooky. Update its status on the Tickets page in Hookd.")
    send_mail(
        f"[Hookd] Ticket #{ticket.pk} from {buyer} on @{account.username}",
        "\n".join(lines),
        settings.DEFAULT_FROM_EMAIL,
        [settings.SUPPORT_EMAIL],
    )
    queries.update_ticket(ticket, emailed_at=timezone.now())
    return "sent"


# Preview


def preview_run(
    *, account: SocialAccount, text: str, post: Post | None = None, voice: str | None = None, knowledge: str | None = None
):
    """What the agent would do with a comment `text`, as a dry run: nothing is stored or sent. `voice` and
    `knowledge` replace the saved ones (unsaved edits on the agent page). Returns a loop.Preview."""
    from apps.agent import loop

    profile = queries.get_or_create_profile(account)
    with attributed(Purpose.REPLY_PREVIEW, account=account, subject=post):
        return loop.preview(account=account, profile=profile, text=text, post=post, voice=voice, knowledge=knowledge)


# Sending


def _take_slot(account: SocialAccount, not_before: datetime | None) -> datetime:
    """The account's next free send slot, shared by public replies and DMs. Call inside a transaction."""
    profile = queries.lock_profile(account)
    now = timezone.now()
    slot = max(moment for moment in (now, profile.next_send_at, profile.paused_until, not_before) if moment)
    spacing = timedelta(seconds=reply_limit("MIN_SPACING_SECONDS") + random.uniform(0, reply_limit("JITTER_SECONDS")))
    queries.update_profile(profile, next_send_at=slot + spacing)
    return slot


def schedule_reply(reply: CommentReply, *, not_before: datetime | None = None) -> CommentReply:
    """Queue a reply in the account's next free send slot and hand it to Celery once the transaction commits."""
    from apps.agent.tasks import send_reply as send_reply_task

    with transaction.atomic():
        slot = _take_slot(reply.comment.account, not_before)
        queries.update_reply(reply, status=ReplyStatus.QUEUED, scheduled_for=slot, error="")
        transaction.on_commit(lambda: send_reply_task.apply_async(args=[reply.pk], eta=slot))
    return reply


def schedule_dm(dm: OutgoingDM, *, not_before: datetime | None = None) -> OutgoingDM:
    from apps.agent.tasks import send_dm as send_dm_task

    with transaction.atomic():
        slot = _take_slot(dm.run.account, not_before)
        queries.update_dm(dm, status=ReplyStatus.QUEUED, scheduled_for=slot, error="")
        transaction.on_commit(lambda: send_dm_task.apply_async(args=[dm.pk], eta=slot))
    return dm


def _cap_frees_at(account: SocialAccount, now: datetime) -> datetime | None:
    """When the hourly or daily cap is reached, the moment a slot frees up again. None when under both caps."""
    frees_at = None
    for window, cap in ((timedelta(hours=1), reply_limit("MAX_PER_HOUR")), (timedelta(days=1), reply_limit("MAX_PER_DAY"))):
        sent = queries.sent_times_since(account, now - window)
        if len(sent) >= cap:
            candidate = sent[len(sent) - cap] + window
            frees_at = max(frees_at, candidate) if frees_at else candidate
    return frees_at


def _hold_for_limits(account: SocialAccount, profile: AgentProfile, now: datetime, reschedule) -> str | None:
    """Reschedule when the account is paused or a cap is reached. Returns the outcome, or None to send now."""
    if is_paused(profile):
        reschedule(profile.paused_until)
        return "paused"
    frees_at = _cap_frees_at(account, now)
    if frees_at:
        reschedule(frees_at)
        return "capped"
    return None


def _pause_account(profile: AgentProfile, account: SocialAccount, exc: RateLimitedError) -> datetime:
    pause_until = timezone.now() + timedelta(minutes=reply_limit("RATE_LIMIT_PAUSE_MINUTES"))
    queries.update_profile(profile, paused_until=pause_until, pause_reason=exc.message)
    logger.warning("Pausing sending for account %s until %s: %s", account.pk, pause_until, exc.message)
    return pause_until


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
    held = _hold_for_limits(account, profile, now, lambda moment: schedule_reply(reply, not_before=moment))
    if held:
        return held

    provider = get_provider(account.platform)
    problems = provider.reply_text_problems(reply.final_text)
    if problems:
        queries.update_reply(reply, status=ReplyStatus.PENDING_REVIEW, error=" ".join(problems))
        return ReplyStatus.PENDING_REVIEW

    try:
        external_id = provider.reply_to_comment(account, reply.comment.external_id, reply.final_text)
    except RateLimitedError as exc:
        schedule_reply(reply, not_before=_pause_account(profile, account, exc))
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


def send_dm(dm_id: int) -> str:
    """Send one queued DM if its slot has come and Meta still allows it. Returns the outcome (stored as task result)."""
    dm = queries.get_dm(dm_id)
    if dm is None:
        return "missing"
    now = timezone.now()
    if dm.status != ReplyStatus.QUEUED:
        return "not_queued"
    if dm.scheduled_for and dm.scheduled_for > now + EARLY_TOLERANCE:
        return "not_due"
    if not queries.claim_dm_for_sending(dm.pk):
        return "not_queued"
    dm.refresh_from_db()
    run = dm.run
    account = run.account
    if account.status != SocialAccount.Status.ACTIVE:
        queries.update_dm(dm, status=ReplyStatus.FAILED, error="The account needs attention on the connections page.")
        return ReplyStatus.FAILED

    profile = queries.get_or_create_profile(account)
    held = _hold_for_limits(account, profile, now, lambda moment: schedule_dm(dm, not_before=moment))
    if held:
        return held

    if not _dm_still_allowed(dm, now):
        queries.update_dm(dm, status=ReplyStatus.FAILED, error="Instagram no longer allows this DM: the time to send it has passed.")
        return ReplyStatus.FAILED

    provider = get_provider(account.platform)
    try:
        if dm.kind == DMKind.PRIVATE_REPLY:
            sent = provider.send_private_reply(account, run.comment.external_id, dm.text)
        else:
            sent = provider.send_direct_message(account, run.conversation.participant_external_id, dm.text)
    except RateLimitedError as exc:
        schedule_dm(dm, not_before=_pause_account(profile, account, exc))
        return "rate_limited"
    except TokenExpiredError as exc:
        account_queries.set_account_status(account, SocialAccount.Status.EXPIRED, exc.message)
        queries.update_dm(dm, status=ReplyStatus.FAILED, error=exc.message)
        return ReplyStatus.FAILED
    except ProviderAPIError as exc:
        queries.update_dm(dm, status=ReplyStatus.FAILED, error=exc.message)
        return ReplyStatus.FAILED

    queries.update_dm(dm, status=ReplyStatus.SENT, sent_at=timezone.now(), message_external_id=sent.message_id, error="")
    if run.conversation_id is None and sent.recipient_id:
        # The buyer's answer to a private reply arrives in this thread; link it so the run continues there.
        queries.update_run(run, conversation=message_queries.get_or_create_conversation(account, sent.recipient_id))
    return ReplyStatus.SENT


def _dm_still_allowed(dm: OutgoingDM, now: datetime) -> bool:
    run = dm.run
    if dm.kind == DMKind.PRIVATE_REPLY:
        commented_at = run.comment.commented_at or run.created_at
        return now <= commented_at + timedelta(hours=_loop_limit("PRIVATE_REPLY_MAX_HOURS") + 1)
    window_ends = run.conversation.reply_window_ends_at if run.conversation else None
    return bool(window_ends and window_ends > now)


def send_overdue_replies() -> int:
    """Recovery for replies whose Celery task was lost (for example a wiped Redis). Returns how many were sent."""
    sent = 0
    for reply_id in queries.overdue_queued_reply_ids(timezone.now() - OVERDUE_AFTER):
        sent += int(send_reply(reply_id) == ReplyStatus.SENT)
    return sent


def send_overdue_dms() -> int:
    sent = 0
    for dm_id in queries.overdue_queued_dm_ids(timezone.now() - OVERDUE_AFTER):
        sent += int(send_dm(dm_id) == ReplyStatus.SENT)
    return sent


# Inbox


def inbox(user, status: str) -> list[CommentReply]:
    """Newest replies with this status. Each gets `post` (the stored post its comment is on, or None) and `run`
    (the agent run that handled the comment, with its actions, or None)."""
    replies = queries.list_replies_for_user(user, status, INBOX_PAGE_SIZE)
    posts = post_queries.posts_for_user_by_external_id(
        user, {reply.comment.media_external_id for reply in replies if reply.comment.media_external_id}
    )
    runs = queries.runs_for_comments({reply.comment_id for reply in replies})
    for reply in replies:
        reply.post = posts.get((reply.comment.account_id, reply.comment.media_external_id))
        reply.run = runs.get(reply.comment_id)
    return replies


def inbox_counts(user) -> dict[str, int]:
    return queries.reply_counts_for_user(user)


def open_questions(user) -> list[SellerQuestion]:
    return queries.open_questions_for_user(user, INBOX_PAGE_SIZE)


def open_question_count(user) -> int:
    return queries.open_question_count(user)


def pending_dm_count(user) -> int:
    return queries.pending_dm_count(user)


def pending_dms(user) -> list[OutgoingDM]:
    return queries.pending_dms_for_user(user, INBOX_PAGE_SIZE)


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


def get_user_dm(*, user, dm_id: int) -> OutgoingDM:
    dm = queries.get_dm_for_user(user, dm_id)
    if dm is None:
        raise ReplyNotFound()
    return dm


def approve_dm(dm: OutgoingDM, text: str) -> OutgoingDM:
    if dm.status != ReplyStatus.PENDING_REVIEW:
        raise ReplyNotEditable()
    text = text.strip()
    problems = get_provider(dm.run.account.platform).dm_text_problems(text)
    if problems:
        raise ReplyTextRejected(" ".join(problems))
    queries.update_dm(dm, text=text)
    return schedule_dm(dm)


def reject_dm(dm: OutgoingDM) -> OutgoingDM:
    if dm.status != ReplyStatus.PENDING_REVIEW:
        raise ReplyNotEditable()
    if dm.kind == DMKind.PRIVATE_REPLY:
        # The private reply was never sent, so it can still be used.
        queries.update_run(dm.run, private_reply_used_at=None)
    return queries.update_dm(dm, status=ReplyStatus.REJECTED)


def get_user_question(*, user, question_id: int) -> SellerQuestion:
    question = queries.get_question_for_user(user, question_id)
    if question is None:
        raise QuestionNotFound()
    return question


def answer_question(question: SellerQuestion, answer: str) -> SellerQuestion:
    """The seller answered Hooky: the run picks up where it stopped."""
    return _close_question(question, QuestionStatus.ANSWERED, answer.strip())


def dismiss_question(question: SellerQuestion) -> SellerQuestion:
    """The seller does not want to answer: Hooky is told so and wraps up."""
    return _close_question(question, QuestionStatus.DISMISSED, "")


def _close_question(question: SellerQuestion, status: str, answer: str) -> SellerQuestion:
    if question.status != QuestionStatus.OPEN:
        raise QuestionNotOpen()
    with transaction.atomic():
        queries.update_question(question, status=status, answer=answer, answered_at=timezone.now())
        queries.add_event(question.run, EventKind.SELLER_ANSWER, {"question_id": question.pk})
        _queue_advance(question.run)
    return question


# Tickets


def tickets(user, status: str) -> list[SupportTicket]:
    return queries.tickets_for_user(user, status, INBOX_PAGE_SIZE)


def ticket_counts(user) -> dict[str, int]:
    return queries.ticket_counts_for_user(user)


def get_user_ticket(*, user, ticket_id: int) -> SupportTicket:
    ticket = queries.get_ticket_for_user(user, ticket_id)
    if ticket is None:
        raise TicketNotFound()
    return ticket


def update_ticket(ticket: SupportTicket, *, status: str, seller_note: str) -> SupportTicket:
    return queries.update_ticket(ticket, status=status, seller_note=seller_note.strip())


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


def buyer_message_text(message: DirectMessage) -> str:
    """A buyer DM as the agent reads it."""
    if message.kind == MessageKind.STORY_MENTION:
        return "(mentioned the account in their story)"
    if message.kind == MessageKind.STORY_REPLY:
        return f"(replying to the account's story) {message.text}".strip()
    if message.kind == MessageKind.SHARE:
        post_id = f"post_id {message.shared_post_external_id}" if message.shared_post_external_id else "no post id"
        caption = " ".join(message.shared_post_caption.split())[:SHARED_CAPTION_PREVIEW]
        return f'(shared a post, {post_id}, caption: "{caption or "none"}") {message.text}'.strip()
    if message.kind == MessageKind.MEDIA:
        return f"(sent a photo or video) {message.text}".strip()
    return message.text or "(a message Hookd cannot read)"

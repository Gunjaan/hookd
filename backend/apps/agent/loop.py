"""The agent loop: turns what happened on a run (a comment, a buyer DM, the seller's answer, a follow-up coming due)
into a user turn, then lets the model call tools until it is done, asks the seller, or runs out of steps.

Every run is bounded: MAX_STEPS model calls per user turn (more fails the run), MAX_RUN_STEPS in its whole life (then
the buyer is handed to the seller and the run never wakes again), and any error ends the run instead of leaving it
"running". A follow-up that can no longer help is dropped without calling the model, and a follow-up cannot schedule
another one.

The transcript (`AgentRun.messages`) is stored after every step, assistant turns exactly as the model returned them
(thinking blocks included), so a run can stop for hours and pick up where it left off. A run that asked the seller ends
on an assistant turn with an unanswered tool_use: it only moves on once the answer arrives, and the other results of
that turn (`held_results`) go back together with it.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from django.conf import settings
from django.utils import timezone

from apps.agent import model_querysets as queries
from apps.agent import services, tools
from apps.agent.constants import EventKind, QuestionStatus, RunStatus
from apps.agent.engines import prompts
from apps.agent.engines.base import AgentModel
from apps.agent.engines.registry import get_model
from apps.agent.models import AgentEvent, AgentProfile, AgentRun
from apps.ai.constants import Purpose
from apps.ai.services import attributed
from apps.comments.models import Comment
from apps.content import model_querysets as post_queries
from apps.content.models import Post
from apps.core.exceptions import HookdError
from apps.integrations.models import SocialAccount
from apps.messaging import model_querysets as message_queries

logger = logging.getLogger(__name__)

SELLER_ANSWER_PREFIX = "The seller answered: "
SELLER_DISMISSED = "The seller chose not to answer. Do not tell the buyer anything about it; wrap up."
SELLER_SILENT = (
    "The seller did not answer in time. Do not guess. If the buyer is waiting for this, tell them kindly that the "
    "seller will get back to them, then wrap up."
)
FOLLOWUP_RULES = (
    "This is a follow-up: send at most one short DM with something new and useful, or do nothing if the buyer already "
    "got what they needed, said no, bought, or complained. You cannot schedule another follow-up."
)
DROP_FOLLOWUP = "drop_followup"
DEFAULT_VOICE = "not set: friendly and short"
OUTCOME_MAX_LENGTH = 500


class StepLimitReached(HookdError):
    default_message = "Hooky took too many steps without finishing."


class RunTooLong(HookdError):
    default_message = "Hooky has handled this buyer for a long time, so it handed the conversation to you."


def _limit(name: str):
    return settings.AGENT_LOOP_LIMITS[name]


@dataclass
class TrailItem:
    tool: str
    input: dict
    result: str
    is_error: bool


@dataclass
class Preview:
    trail: list[TrailItem] = field(default_factory=list)
    outcome: str = ""
    asked_seller: bool = False


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _start_text(run: AgentRun, voice: str, now: datetime) -> str:
    """The first user turn of a run started by a comment."""
    comment = run.comment
    lines = [
        "A buyer commented on one of the seller's posts.",
        f"Account: @{run.account.username}",
        f"Seller's voice: {_one_line(voice) or DEFAULT_VOICE}",
        f"Post id: {comment.media_external_id or 'unknown'}",
        f"Commenter: @{comment.author_username}",
        f"Comment: {_one_line(comment.text)}",
    ]
    if comment.parent_external_id:
        lines.append("It is a reply inside a comment thread.")
    lines.append(f"Now: {now:%Y-%m-%d %H:%M} UTC")
    return "\n".join(lines)


def _dm_start_text(run: AgentRun, voice: str, now: datetime) -> str:
    """The first user turn of a run started by a DM (the DM itself follows as its own event)."""
    buyer = f"@{run.buyer_username}" if run.buyer_username else "unknown username"
    return "\n".join(
        [
            "A buyer wrote the seller a DM.",
            f"Account: @{run.account.username}",
            f"Seller's voice: {_one_line(voice) or DEFAULT_VOICE}",
            f"Buyer: {buyer}",
            f"Now: {now:%Y-%m-%d %H:%M} UTC",
        ]
    )


def _dm_status(run: AgentRun, now: datetime) -> str:
    kind, reason = services.dm_route(run, now)
    if kind is None:
        return f"No DM is allowed now: {reason}"
    return "A DM is allowed now." if kind == "dm" else "A DM is allowed now (the private reply to the comment)."


def _event_text(run: AgentRun, event: AgentEvent, voice: str, now: datetime) -> str:
    if event.kind == EventKind.COMMENT:
        return _start_text(run, voice, now)
    if event.kind == EventKind.DM:
        message = message_queries.get_message(event.payload.get("message_id"))
        text = services.buyer_message_text(message) if message else "(a message that is no longer there)"
        return f"New DM from the buyer at {now:%H:%M} UTC: {text}\n{_dm_status(run, now)}"
    note = event.payload.get("note", "")
    return f"Follow-up due ({now:%Y-%m-%d %H:%M} UTC). Your note: {note}\n{_dm_status(run, now)}\n{FOLLOWUP_RULES}"


def _awaiting_seller(run: AgentRun) -> bool:
    """The transcript ends on tool calls with no results yet: the run asked the seller and waits for the answer."""
    if not run.messages or run.messages[-1]["role"] != "assistant":
        return False
    return any(block.get("type") == "tool_use" for block in run.messages[-1]["content"])


def _answer_result(run: AgentRun, event: AgentEvent) -> dict | None:
    question = queries.get_question(event.payload.get("question_id"))
    if question is None or question.run_id != run.pk:
        return None
    if question.status == QuestionStatus.DISMISSED:
        content = SELLER_DISMISSED
    elif question.status == QuestionStatus.EXPIRED:
        content = SELLER_SILENT
    else:
        content = f"{SELLER_ANSWER_PREFIX}{question.answer}"
    return {"type": "tool_result", "tool_use_id": question.tool_use_id, "content": content}


def _user_turn(run: AgentRun, events: list[AgentEvent], voice: str, now: datetime) -> list[dict] | None:
    """The next user turn built from due events, or None when there is nothing to do yet (no events, or the run
    waits for the seller and the answer has not come)."""
    answers = [event for event in events if event.kind == EventKind.SELLER_ANSWER]
    others = [event for event in events if event.kind != EventKind.SELLER_ANSWER]
    blocks = []
    if _awaiting_seller(run):
        result = next((result for result in map(lambda event: _answer_result(run, event), answers) if result), None)
        if result is None:
            return None
        blocks = [*run.held_results, result]
    if not run.messages and run.comment is None and others:
        blocks.append({"type": "text", "text": _dm_start_text(run, voice, now)})
    blocks += [{"type": "text", "text": _event_text(run, event, voice, now)} for event in others]
    return blocks or None


def _last_text(content: list[dict]) -> str:
    texts = [block["text"] for block in content if block.get("type") == "text" and block.get("text", "").strip()]
    return _one_line(texts[-1])[:OUTCOME_MAX_LENGTH] if texts else ""


def _turns(ctx: tools.ToolContext, model: AgentModel, on_action: Callable, save: Callable) -> str:
    """Model steps until the model stops calling tools ("ended") or asks the seller ("paused"). At most MAX_STEPS
    calls, and never past the run's MAX_RUN_STEPS."""
    run = ctx.run
    steps_left = _limit("MAX_RUN_STEPS") - run.steps
    if steps_left <= 0:
        raise RunTooLong()
    for _ in range(min(_limit("MAX_STEPS"), steps_left)):
        step = model.step(system=prompts.AGENT_SYSTEM, tools=prompts.TOOLS, messages=run.messages)
        run.messages.append({"role": "assistant", "content": step.content})
        run.steps += 1
        uses = [block for block in step.content if block.get("type") == "tool_use"]
        if not uses:
            run.outcome = _last_text(step.content) or run.outcome
            save()
            return "ended"
        ctx.batch_results, ctx.asked_seller = [], False
        paused = False
        for block in uses:
            ctx.tool_use_id = block["id"]
            arguments = block.get("input") or {}
            result = tools.execute(ctx, block["name"], arguments)
            on_action(block["name"], arguments, result)
            if result.pauses:
                paused = True
                continue
            entry = {"type": "tool_result", "tool_use_id": block["id"], "content": result.text}
            if result.is_error:
                entry["is_error"] = True
            ctx.batch_results.append(entry)
        if paused:
            run.held_results = ctx.batch_results
            save()
            return "paused"
        run.messages.append({"role": "user", "content": ctx.batch_results})
        save()
    raise RunTooLong() if run.steps >= _limit("MAX_RUN_STEPS") else StepLimitReached()


def _context(run: AgentRun) -> tools.ToolContext:
    profile = queries.get_or_create_profile(run.account)
    if run.comment_id:
        post_id = run.comment.media_external_id
    elif run.conversation_id:
        post_id = message_queries.latest_shared_post_id(run.conversation)
    else:
        post_id = ""
    post = post_queries.find_post(run.account, post_id)
    return tools.ToolContext(run=run, profile=profile, post=post)


def _followup_drop_reason(run: AgentRun, now: datetime) -> str:
    if services.dm_route(run, now)[0] is None:
        return "Dropped the follow-up: Instagram no longer allows a DM to this buyer."
    return ""


def _drop_useless_followups(run: AgentRun, events: list[AgentEvent], now: datetime, on_action: Callable) -> list[AgentEvent]:
    """Follow-ups that can no longer help are closed without calling the model. Returns the events left."""
    kept = []
    for event in events:
        reason = _followup_drop_reason(run, now) if event.kind == EventKind.FOLLOWUP else ""
        if reason:
            queries.mark_events_processed([event])
            on_action(DROP_FOLLOWUP, {"note": event.payload.get("note", "")}, tools.ToolResult(reason))
        else:
            kept.append(event)
    return kept


def advance(run_id: int) -> str:
    """Handle everything due on a run. Only one worker runs a run at a time; one that finds it busy leaves its event
    to the worker holding it (a worker that died is taken over after STALE_RUN_MINUTES). Returns the run's status."""
    stale_before = timezone.now() - timedelta(minutes=_limit("STALE_RUN_MINUTES"))
    if queries.claim_run(run_id, stale_before=stale_before) is None:
        return "busy"
    run = queries.get_run(run_id)

    def save() -> None:
        queries.update_run(run, messages=run.messages, held_results=run.held_results, steps=run.steps, outcome=run.outcome)

    def on_action(tool: str, arguments: dict, result: tools.ToolResult) -> None:
        queries.add_action(run, tool=tool, input=arguments, result=result.text, is_error=result.is_error)

    try:
        ctx = _context(run)
        with attributed(Purpose.COMMENT_AGENT, account=run.account, subject=run.comment or run):
            while True:
                now = timezone.now()
                events = _drop_useless_followups(run, queries.due_events(run, now), now, on_action)
                turn = _user_turn(run, events, ctx.profile.voice, now)
                if turn is None:
                    if not _awaiting_seller(run):
                        queries.mark_events_processed(events)  # nothing for the agent in them (a closed question)
                    break
                queries.mark_events_processed(events)
                ctx.woken_by_followup = all(event.kind == EventKind.FOLLOWUP for event in events)
                run.messages.append({"role": "user", "content": turn})
                run.held_results = []
                save()
                if _turns(ctx, get_model(), on_action, save) == "paused":
                    break
    except RunTooLong as exc:
        return _finish(run, RunStatus.HANDED_OFF, exc.message)
    except HookdError as exc:
        logger.warning("Agent run %s failed: %s", run.pk, exc.message)
        return _finish(run, RunStatus.FAILED, exc.message)
    except Exception:
        # A bug must never leave the run "running" (that buyer would never be answered again).
        logger.exception("Agent run %s crashed", run.pk)
        return _finish(run, RunStatus.FAILED, "Something went wrong inside Hookd.")
    return _settle(run)


def _finish(run: AgentRun, status: str, message: str) -> str:
    """End a run for good: handed to the seller or failed. It never wakes again; a comment waits in the inbox."""
    queries.update_run(
        run, status=status, error=message[:500], wake_at=None, messages=run.messages, held_results=[], steps=run.steps,
        outcome=message[:OUTCOME_MAX_LENGTH] if status == RunStatus.HANDED_OFF else run.outcome,
    )
    queries.drop_pending_events(run)
    services.note_run_failed(run, message)
    return status


def _settle(run: AgentRun) -> str:
    """Release the run with the status that says what it waits for, and catch events that came in meanwhile."""
    now = timezone.now()
    wake_at = queries.next_event_time(run, now)
    if _awaiting_seller(run):
        status = RunStatus.WAITING_SELLER
    else:
        status = RunStatus.WAITING_FOLLOWUP if wake_at else RunStatus.DONE
    queries.update_run(run, status=status, wake_at=wake_at)
    if status != RunStatus.WAITING_SELLER:
        services.note_run_finished(run)
    if _user_turn(run, queries.due_events(run, now), "", now) is not None:
        services.advance_run_later(run)
    return status


def preview(
    *, account: SocialAccount, profile: AgentProfile, text: str, post: Post | None, voice: str | None, knowledge: str | None
) -> Preview:
    """A dry run on a made up comment: tools only say what they would do, nothing is stored or sent."""
    now = timezone.now()
    comment = Comment(
        account=account, text=text, author_username="customer", media_external_id=post.external_id if post else "",
        commented_at=now,
    )
    run = AgentRun(account=account, comment=comment, messages=[], held_results=[])
    ctx = tools.ToolContext(run=run, profile=profile, post=post, dry_run=True, knowledge=knowledge)
    result = Preview()
    run.messages.append(
        {"role": "user", "content": [{"type": "text", "text": _start_text(run, profile.voice if voice is None else voice, now)}]}
    )

    def on_action(tool: str, arguments: dict, tool_result: tools.ToolResult) -> None:
        result.trail.append(TrailItem(tool, arguments, tool_result.text, tool_result.is_error))

    state = _turns(ctx, get_model(), on_action, lambda: None)
    result.outcome, result.asked_seller = run.outcome, state == "paused"
    return result

"""The agent's tools: what each tool call does, and the checks that never trust the model.

Every tool returns a ToolResult whose text goes back to the model as the tool_result. Problems (text Instagram would
refuse, numbers no tool gave, Meta's messaging rules, limits) come back as errors so the agent can correct itself.
In a dry run (the agent page's "Try it") tools that would change something store and send nothing, but answer as if
they had, so the model behaves exactly as it would for real.
"""

import inspect
import re
from dataclasses import dataclass, field
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from apps.agent import model_querysets as queries
from apps.agent import services
from apps.agent.constants import FactScope, FactSource, ReplyStatus
from apps.agent.models import AgentProfile, AgentRun
from apps.content import model_querysets as post_queries
from apps.content.models import Post
from apps.integrations.constants import Capability
from apps.integrations.providers.registry import get_provider

NUMBER_PATTERN = re.compile(r"\d[\d,]*(?:\.\d+)?")
HANDLE_PATTERN = re.compile(r"@[\w.]+")
LABEL_MAX_LENGTH = 80
FACT_MAX_LENGTH = 300


@dataclass
class ToolResult:
    text: str
    is_error: bool = False
    pauses: bool = False  # ask_seller: the run stops until the seller answers


@dataclass
class ToolContext:
    run: AgentRun  # unsaved in a dry run
    profile: AgentProfile
    post: Post | None
    dry_run: bool = False
    knowledge: str | None = None  # dry run: the unsaved account brain from the agent page
    tool_use_id: str = ""
    batch_results: list[dict] = field(default_factory=list)  # results of this turn so far
    asked_seller: bool = False
    woken_by_followup: bool = False  # this turn is a follow-up coming due: no chaining another one
    dry_tickets: list[str] = field(default_factory=list)  # dry run: tickets "opened" so far, for check_tickets


def _limit(name: str):
    return settings.AGENT_LOOP_LIMITS[name]


def _error(text: str) -> ToolResult:
    return ToolResult(text, is_error=True)


# Number guard


def _numbers(text: str) -> set[str]:
    """Numbers as plain digits: "Rs 1,299" and "1299.00" are both "1299"."""
    found = set()
    for match in NUMBER_PATTERN.findall(text):
        number = match.replace(",", "")
        if "." in number:
            number = number.rstrip("0").rstrip(".")
        found.add(number)
    return found


def _block_texts(content) -> list[str]:
    if isinstance(content, str):
        return [content]
    texts = []
    for block in content:
        if block.get("type") == "text":
            texts.append(block["text"])
        elif block.get("type") == "tool_result":
            texts.extend(_block_texts(block.get("content") or ""))
    return texts


def _evidence(ctx: ToolContext) -> str:
    """Everything the agent was told in this run: the comment, buyer DMs, tool results and the seller's answers."""
    texts = []
    for message in ctx.run.messages:
        if message["role"] == "user":
            texts.extend(_block_texts(message["content"]))
    texts.extend(_block_texts(ctx.batch_results))
    return "\n".join(texts)


def unbacked_numbers(ctx: ToolContext, text: str) -> list[str]:
    """Numbers in `text` (ignoring @handles) that no tool result, comment, DM or seller answer contains."""
    return sorted(_numbers(HANDLE_PATTERN.sub(" ", text)) - _numbers(_evidence(ctx)))


def _number_problems(ctx: ToolContext, text: str) -> list[str]:
    unbacked = unbacked_numbers(ctx, text)
    if not unbacked:
        return []
    return [f"{', '.join(unbacked)} is not in the brain, use search_brain or ask_seller."]


# Tools


def search_brain(ctx: ToolContext, topic: str) -> ToolResult:
    shop, post = services.brain_parts(ctx.run.account, ctx.profile, ctx.post, knowledge=ctx.knowledge)
    if not shop and not post:
        return ToolResult("The brain is empty: the seller has not told you anything yet. Ask the seller if the buyer needs a fact.")
    sections = [f"Shop facts:\n{shop or 'none'}"]
    if ctx.post is not None:
        sections.append(f"This post's facts:\n{post or 'none'}")
    return ToolResult("\n\n".join(sections))


def get_post(ctx: ToolContext, post_id: str) -> ToolResult:
    if not post_id or (ctx.post is not None and post_id == ctx.post.external_id):
        post = ctx.post
    else:
        post = post_queries.find_post(ctx.run.account, post_id)
    if post is None:
        return _error("Post not found. Only the seller's own posts can be read, use the caption you were given.")
    lines = [
        f"Post id: {post.external_id}",
        f"Type: {post.get_product_type_display()}, {post.get_media_type_display()}",
        f"Link: {post.permalink or 'none'}",
        f"Caption: {post.caption.strip()[:2200] or 'none'}",
        f"Video brain:\n{services.post_brain_text(post) or 'none'}",
    ]
    return ToolResult("\n".join(lines))


def reply_to_comment(ctx: ToolContext, text: str) -> ToolResult:
    run = ctx.run
    account, comment = run.account, run.comment
    if comment is None:
        return _error("This buyer wrote a DM, there is no comment to reply to. Use send_dm.")
    if not services.supports_replies(account):
        return _error("Public replies are not available on this platform.")
    text = text.strip()
    if comment.author_username and not text.startswith("@"):
        text = f"@{comment.author_username} {text}"
    existing = None if ctx.dry_run else queries.get_comment_reply(comment)
    if existing is not None and existing.status != ReplyStatus.SKIPPED:
        return _error("You already replied publicly to this comment.")
    problems = get_provider(account.platform).reply_text_problems(text) + _number_problems(ctx, text)
    if problems:
        return _error(" ".join(problems))
    if not ctx.dry_run and services.same_text_count(account, text) >= services.reply_limit("MAX_SAME_TEXT_PER_HOUR"):
        return _error("This exact reply already went out several times this hour. Say it differently.")
    if ctx.dry_run:
        return ToolResult("Reply scheduled, goes out in about a minute.")
    return ToolResult(services.agent_reply(run, existing, text, approve=ctx.profile.approve_before_sending))


def send_dm(ctx: ToolContext, text: str) -> ToolResult:
    run = ctx.run
    provider = get_provider(run.account.platform)
    text = text.strip()
    kind, reason = services.dm_route(run, timezone.now())
    if kind is None:
        return _error(f"DM not allowed now: {reason}")
    needed = Capability.PRIVATE_REPLY if kind == "private_reply" else Capability.DIRECT_MESSAGE
    if needed not in provider.capabilities:
        return _error("DMs are not available on this platform.")
    problems = provider.dm_text_problems(text) + _number_problems(ctx, text)
    if problems:
        return _error(" ".join(problems))
    if ctx.dry_run:
        return ToolResult(f"{'Private reply' if kind == 'private_reply' else 'DM'} scheduled, goes out in about a minute.")
    return ToolResult(services.agent_dm(run, kind, text, approve=ctx.profile.approve_before_sending))


def ask_seller(ctx: ToolContext, question: str) -> ToolResult:
    question = question.strip()
    if not question:
        return _error("The question is empty.")
    if ctx.asked_seller:
        return _error("Ask one question at a time.")
    if not ctx.dry_run and queries.question_count(ctx.run) >= _limit("MAX_QUESTIONS"):
        return _error("You already asked the seller enough about this buyer. Use what you know, or leave it.")
    ctx.asked_seller = True
    if ctx.dry_run:
        return ToolResult("Question sent to the seller.", pauses=True)
    queries.create_question(ctx.run, tool_use_id=ctx.tool_use_id, question=question)
    return ToolResult("Question sent to the seller.", pauses=True)


def save_fact(ctx: ToolContext, label: str, fact: str, scope: str) -> ToolResult:
    label = " ".join(label.split())[:LABEL_MAX_LENGTH]
    fact = " ".join(fact.split())[:FACT_MAX_LENGTH]
    if not label or not fact:
        return _error("A fact needs a label and a text.")
    post = ctx.post if scope == FactScope.POST else None
    if ctx.dry_run:
        return ToolResult(f"Saved for {'this post' if post else 'all posts'}.")
    queries.save_learned_facts(
        ctx.run.account, [(post, label, fact)], source=FactSource.AGENT, source_post=ctx.post, source_run=ctx.run
    )
    return ToolResult(f"Saved for {'this post' if post else 'all posts'}.")


def create_ticket(ctx: ToolContext, product: str, summary: str) -> ToolResult:
    product, summary = product.strip(), summary.strip()
    if not summary:
        return _error("The summary is empty.")
    if not product:
        return _error("Which product is it about? Ask the buyer first, then create the ticket.")
    if not ctx.dry_run and ctx.run.buyer_external_id:
        existing = queries.open_ticket_for_product(ctx.run.account, ctx.run.buyer_external_id, product)
        if existing is not None:
            return _error(
                f"Ticket #{existing.pk} about {existing.product} is already {existing.get_status_display().lower()}. "
                "Share its status instead of opening another."
            )
    if ctx.dry_run:
        ctx.dry_tickets.append(f"#0 opened today, {product}: {summary} Status: Open.")
        return ToolResult("Ticket #0 opened and sent to the support team. Status: Open.")
    ticket = services.create_ticket(ctx.run, product=product, summary=summary)
    return ToolResult(f"Ticket #{ticket.pk} opened and sent to the support team. Status: {ticket.get_status_display()}.")


def check_tickets(ctx: ToolContext) -> ToolResult:
    if ctx.dry_run:
        return ToolResult("\n".join(ctx.dry_tickets) or "This buyer has no tickets.")
    tickets = services.buyer_tickets(ctx.run)
    if not tickets:
        return ToolResult("This buyer has no tickets.")
    lines = []
    for ticket in tickets:
        line = f"#{ticket.pk} opened {ticket.created_at:%Y-%m-%d}, {ticket.product or 'product not given'}: {ticket.summary} Status: {ticket.get_status_display()}."
        if ticket.seller_note:
            line += f" Seller's note for the buyer: {ticket.seller_note}"
        lines.append(line)
    return ToolResult("\n".join(lines))


def schedule_followup(ctx: ToolContext, hours: int, note: str) -> ToolResult:
    run = ctx.run
    now = timezone.now()
    if ctx.woken_by_followup:
        return _error("This is already a follow-up. Do not chase the buyer again; leave it.")
    if not isinstance(hours, int) or not 1 <= hours <= _limit("FOLLOWUP_MAX_HOURS"):
        return _error(f"hours must be between 1 and {_limit('FOLLOWUP_MAX_HOURS')}.")
    if run.followups_used >= _limit("MAX_FOLLOWUPS"):
        return _error("No more follow-ups for this buyer.")
    if run.wake_at and run.wake_at > now:
        return _error(f"A follow-up is already scheduled for {run.wake_at:%Y-%m-%d %H:%M} UTC.")
    due = now + timedelta(hours=hours)
    allowed_until = services.dm_allowed_until(run, now)
    if allowed_until is None:
        return _error("Instagram would not allow a DM to this buyer any more, so a follow-up cannot help.")
    if due > allowed_until:
        return _error(f"A DM is only allowed until {allowed_until:%Y-%m-%d %H:%M} UTC. Pick fewer hours.")
    if ctx.dry_run:
        return ToolResult(f"Follow-up scheduled for {due:%Y-%m-%d %H:%M} UTC.")
    services.schedule_followup(run, due, note.strip())
    return ToolResult(f"Follow-up scheduled for {due:%Y-%m-%d %H:%M} UTC.")


TOOLS = {
    "search_brain": search_brain,
    "get_post": get_post,
    "reply_to_comment": reply_to_comment,
    "send_dm": send_dm,
    "ask_seller": ask_seller,
    "save_fact": save_fact,
    "create_ticket": create_ticket,
    "check_tickets": check_tickets,
    "schedule_followup": schedule_followup,
}


def execute(ctx: ToolContext, name: str, arguments: dict) -> ToolResult:
    tool = TOOLS.get(name)
    if tool is None:
        return _error(f"Unknown tool {name}.")
    try:
        inspect.signature(tool).bind(ctx, **arguments)
    except TypeError:
        return _error(f"Wrong arguments for {name}.")
    return tool(ctx, **arguments)

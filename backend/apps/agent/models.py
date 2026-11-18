import re

from django.db import models

from apps.agent.constants import DMKind, EventKind, FactSource, QuestionStatus, ReplyStatus, RunStatus, TicketStatus
from apps.comments.models import Comment
from apps.content.models import Post
from apps.core.models import TimeStampedModel
from apps.integrations.models import SocialAccount
from apps.messaging.models import Conversation

MENTION_PATTERN = re.compile(r"^@\S+\s*")


class AgentProfile(TimeStampedModel):
    """Reply agent settings and account brain for one connected account."""

    account = models.OneToOneField(SocialAccount, on_delete=models.CASCADE, related_name="agent_profile")
    enabled = models.BooleanField(default=False)
    voice = models.TextField(blank=True, help_text="How replies should sound: tone, slang, emojis, sign-off.")
    knowledge = models.TextField(blank=True, help_text="Account brain: facts the agent may use, one per line.")
    reply_to_threads = models.BooleanField(default=False, help_text="Also answer replies inside comment threads.")
    approve_before_sending = models.BooleanField(
        default=False, help_text="Every reply and DM Hooky writes waits in the inbox for approval."
    )
    paused_until = models.DateTimeField(null=True, blank=True, help_text="Sending is paused until then (rate limited).")
    pause_reason = models.CharField(max_length=500, blank=True)
    next_send_at = models.DateTimeField(null=True, blank=True, help_text="Earliest time the next reply may go out.")

    def __str__(self) -> str:
        return f"Agent for {self.account}"


class PostBrain(TimeStampedModel):
    """Video brain: facts about one post the agent may use, and a per-post on/off switch."""

    post = models.OneToOneField(Post, on_delete=models.CASCADE, related_name="brain")
    knowledge = models.TextField(blank=True, help_text="Facts about this post, one per line.")
    agent_enabled = models.BooleanField(default=True)
    caption_learned_at = models.DateTimeField(
        null=True, blank=True, help_text="When the agent read this post's caption. Set once, so it reads it only once."
    )

    def __str__(self) -> str:
        return f"Brain for {self.post}"


class LearnedFact(TimeStampedModel):
    """A fact the agent picked up on its own, from a post caption or a reply the creator wrote in the inbox."""

    account = models.ForeignKey(SocialAccount, on_delete=models.CASCADE, related_name="learned_facts")
    post = models.ForeignKey(
        Post, on_delete=models.CASCADE, null=True, blank=True, related_name="learned_facts",
        help_text="Set for facts about one post, empty for facts about the whole shop.",
    )
    label = models.CharField(max_length=80)
    text = models.CharField(max_length=300)
    source = models.CharField(max_length=16, choices=FactSource.choices)
    source_post = models.ForeignKey(
        Post, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
        help_text="Post whose caption or comment the fact came from.",
    )
    source_reply = models.ForeignKey(
        "CommentReply", on_delete=models.SET_NULL, null=True, blank=True, related_name="learned_facts",
        help_text="Inbox reply the creator wrote that taught this fact.",
    )
    source_run = models.ForeignKey(
        "AgentRun", on_delete=models.SET_NULL, null=True, blank=True, related_name="learned_facts",
        help_text="Agent run that saved this fact (save_fact).",
    )

    class Meta:
        ordering = ["-created_at", "-pk"]

    def __str__(self) -> str:
        return f"{self.label}: {self.text}"

    @property
    def line(self) -> str:
        return f"{self.label}: {self.text}"


class CommentReply(TimeStampedModel):
    """What the agent decided for one comment, and the reply's journey to the platform."""

    comment = models.OneToOneField(Comment, on_delete=models.CASCADE, related_name="agent_reply")
    status = models.CharField(max_length=16, choices=ReplyStatus.choices)
    draft_text = models.TextField(blank=True, help_text="What the agent wrote.")
    final_text = models.TextField(blank=True, help_text="What is or was sent.")
    decision = models.JSONField(default=dict, blank=True, help_text="Intent, language, confidence, reason, engine.")
    scheduled_for = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    reply_external_id = models.CharField(max_length=128, blank=True)
    error = models.CharField(max_length=500, blank=True)
    attempts = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "comment replies"

    def __str__(self) -> str:
        return f"{self.get_status_display()}: {self.comment}"

    @property
    def written_by_creator(self) -> bool:
        """The creator changed Hooky's draft (or wrote the reply because Hooky could not)."""
        def normalized(text: str) -> str:
            return MENTION_PATTERN.sub("", text).strip().lower()

        return normalized(self.final_text) != normalized(self.draft_text)


class AgentRun(TimeStampedModel):
    """One agent loop with one buyer: started by their comment or their DM. Holds the Claude transcript, its state,
    and the DM thread it continues."""

    account = models.ForeignKey(SocialAccount, on_delete=models.CASCADE, related_name="agent_runs")
    comment = models.OneToOneField(
        Comment, on_delete=models.CASCADE, null=True, blank=True, related_name="agent_run",
        help_text="The comment that started the run. Empty when a DM started it.",
    )
    conversation = models.ForeignKey(
        Conversation, on_delete=models.SET_NULL, null=True, blank=True, related_name="agent_runs",
        help_text="DM thread with the buyer, set once a DM goes out or the buyer writes.",
    )
    buyer_external_id = models.CharField(max_length=128, blank=True, help_text="The buyer's platform scoped id.")
    buyer_username = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=16, choices=RunStatus.choices, default=RunStatus.QUEUED)
    messages = models.JSONField(default=list, blank=True, help_text="Claude transcript, assistant turns unchanged.")
    held_results = models.JSONField(
        default=list, blank=True, help_text="Tool results of the turn that asked the seller, sent with the answer."
    )
    steps = models.PositiveIntegerField(default=0)
    followups_used = models.PositiveIntegerField(default=0)
    private_reply_used_at = models.DateTimeField(null=True, blank=True)
    wake_at = models.DateTimeField(null=True, blank=True, help_text="When a scheduled follow-up wakes the run.")
    outcome = models.CharField(max_length=500, blank=True, help_text="What Hooky did, in one line.")
    error = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.get_status_display()}: {self.comment or self.buyer_username or self.buyer_external_id}"


class AgentEvent(TimeStampedModel):
    """Something that wakes a run: the comment, the seller's answer, a buyer DM, a follow-up coming due."""

    run = models.ForeignKey(AgentRun, on_delete=models.CASCADE, related_name="events")
    kind = models.CharField(max_length=16, choices=EventKind.choices)
    payload = models.JSONField(default=dict, blank=True)
    available_at = models.DateTimeField(null=True, blank=True, help_text="Not handled before then (follow-ups).")
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["pk"]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} for run {self.run_id}"


class AgentAction(TimeStampedModel):
    """One tool call the agent made and what came back: the trail shown as "What Hooky did"."""

    run = models.ForeignKey(AgentRun, on_delete=models.CASCADE, related_name="actions")
    tool = models.CharField(max_length=40)
    input = models.JSONField(default=dict, blank=True)
    result = models.TextField(blank=True)
    is_error = models.BooleanField(default=False)

    class Meta:
        ordering = ["created_at", "pk"]

    def __str__(self) -> str:
        return f"{self.tool} in run {self.run_id}"


class SellerQuestion(TimeStampedModel):
    """A question Hooky asked the seller (ask_seller). The run waits until it is answered."""

    run = models.ForeignKey(AgentRun, on_delete=models.CASCADE, related_name="questions")
    tool_use_id = models.CharField(max_length=128)
    question = models.TextField()
    answer = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=QuestionStatus.choices, default=QuestionStatus.OPEN)
    answered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.question[:60]


class OutgoingDM(TimeStampedModel):
    """A DM Hooky sends to the commenter: the private reply to the comment, or a DM inside the open window."""

    run = models.ForeignKey(AgentRun, on_delete=models.CASCADE, related_name="dms")
    kind = models.CharField(max_length=16, choices=DMKind.choices)
    text = models.TextField()
    status = models.CharField(max_length=16, choices=ReplyStatus.choices)
    scheduled_for = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    message_external_id = models.CharField(max_length=255, blank=True)
    error = models.CharField(max_length=500, blank=True)
    attempts = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "outgoing DM"

    def __str__(self) -> str:
        return f"{self.get_status_display()}: {self.text[:40]}"


class SupportTicket(TimeStampedModel):
    """A buyer's problem Hooky passed on (create_ticket): emailed to support and tracked on the Tickets page. Hooky
    reads a buyer's tickets (check_tickets) to tell them where it stands."""

    account = models.ForeignKey(SocialAccount, on_delete=models.CASCADE, related_name="support_tickets")
    run = models.ForeignKey(AgentRun, on_delete=models.SET_NULL, null=True, blank=True, related_name="tickets")
    buyer_external_id = models.CharField(max_length=128, blank=True)
    buyer_username = models.CharField(max_length=255, blank=True)
    product = models.CharField(max_length=255, blank=True)
    summary = models.TextField()
    status = models.CharField(max_length=16, choices=TicketStatus.choices, default=TicketStatus.OPEN)
    seller_note = models.TextField(blank=True, help_text="What the seller tells the buyer about it. Hooky can share it.")
    emailed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"#{self.pk} {self.get_status_display()}: {self.summary[:40]}"

from django.db import models

from apps.agent.constants import FallbackAction, ReplyStatus, RuleAction
from apps.comments.models import Comment
from apps.content.models import Post
from apps.core.models import TimeStampedModel
from apps.integrations.models import SocialAccount


class AgentProfile(TimeStampedModel):
    """Reply agent settings and account brain for one connected account."""

    account = models.OneToOneField(SocialAccount, on_delete=models.CASCADE, related_name="agent_profile")
    enabled = models.BooleanField(default=False)
    fallback_action = models.CharField(
        max_length=16, choices=FallbackAction.choices, default=FallbackAction.REVIEW,
        help_text="What happens to comments no rule matches.",
    )
    voice = models.TextField(blank=True, help_text="How replies should sound: tone, slang, emojis, sign-off.")
    knowledge = models.TextField(blank=True, help_text="Account brain: facts the agent may use, one per line.")
    reply_to_threads = models.BooleanField(default=False, help_text="Also answer replies inside comment threads.")
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

    def __str__(self) -> str:
        return f"Brain for {self.post}"


class ReplyRule(TimeStampedModel):
    """Creator rule: which comments to answer and how. Post rules are checked before account rules."""

    account = models.ForeignKey(SocialAccount, on_delete=models.CASCADE, related_name="reply_rules")
    post = models.ForeignKey(Post, on_delete=models.CASCADE, null=True, blank=True, related_name="reply_rules")
    name = models.CharField(max_length=120)
    when = models.CharField(max_length=500, blank=True, help_text="Plain English condition for the agent.")
    keywords = models.CharField(max_length=500, blank=True, help_text="Comma separated words that trigger the rule.")
    action = models.CharField(max_length=16, choices=RuleAction.choices, default=RuleAction.REVIEW)
    how_to_reply = models.TextField(blank=True, help_text="Instructions or the exact reply.")
    position = models.PositiveIntegerField(default=0)
    enabled = models.BooleanField(default=True)

    class Meta:
        ordering = ["position", "pk"]

    def __str__(self) -> str:
        return self.name

    @property
    def keyword_list(self) -> list[str]:
        return [word.strip() for word in self.keywords.split(",") if word.strip()]


class CommentReply(TimeStampedModel):
    """What the agent decided for one comment, and the reply's journey to the platform."""

    comment = models.OneToOneField(Comment, on_delete=models.CASCADE, related_name="agent_reply")
    status = models.CharField(max_length=16, choices=ReplyStatus.choices)
    rule = models.ForeignKey(ReplyRule, on_delete=models.SET_NULL, null=True, blank=True, related_name="replies")
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

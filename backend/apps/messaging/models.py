from datetime import datetime, timedelta

from django.db import models

from apps.core.models import TimeStampedModel
from apps.integrations.constants import MessageKind
from apps.integrations.models import SocialAccount

# Meta's standard messaging window: the account may answer for 24 hours after the person's last message.
REPLY_WINDOW = timedelta(hours=24)


class Conversation(TimeStampedModel):
    """A direct message thread between a connected account and one person."""

    account = models.ForeignKey(SocialAccount, on_delete=models.CASCADE, related_name="conversations")
    participant_external_id = models.CharField(max_length=128, help_text="The person's platform scoped id.")
    last_customer_message_at = models.DateTimeField(
        null=True, blank=True, help_text="When the person last wrote. Opens the reply window."
    )

    class Meta:
        ordering = ["-last_customer_message_at", "-pk"]
        constraints = [
            models.UniqueConstraint(fields=["account", "participant_external_id"], name="uniq_conversation_participant"),
        ]

    def __str__(self) -> str:
        return f"{self.account} with {self.participant_external_id}"

    @property
    def reply_window_ends_at(self) -> datetime | None:
        return self.last_customer_message_at + REPLY_WINDOW if self.last_customer_message_at else None


class DirectMessage(TimeStampedModel):
    """One message in a conversation, as it arrived from the platform webhook."""

    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="messages")
    external_id = models.CharField(max_length=255, help_text="Platform message id (mid).")
    is_from_account = models.BooleanField(default=False, help_text="Sent by the connected account (echo).")
    kind = models.CharField(max_length=16, choices=MessageKind.choices, default=MessageKind.TEXT)
    text = models.TextField(blank=True)
    story_external_id = models.CharField(max_length=128, blank=True, help_text="The account's story it replies to.")
    media_url = models.TextField(blank=True, help_text="Story or attachment CDN URL. Expires.")
    reply_to_external_id = models.CharField(max_length=255, blank=True)
    shared_post_external_id = models.CharField(max_length=128, blank=True, help_text="The post or reel it shares.")
    shared_post_caption = models.TextField(blank=True, help_text="The shared post's caption, as sent with it.")
    sent_at = models.DateTimeField(null=True, blank=True)
    payload = models.JSONField(default=dict, blank=True, help_text="Raw webhook event, for debugging.")

    class Meta:
        ordering = ["sent_at", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["conversation", "external_id"], name="uniq_message_conversation_external"),
        ]

    def __str__(self) -> str:
        return f"{self.get_kind_display()}: {self.text[:40]}"

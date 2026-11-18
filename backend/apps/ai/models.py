from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models

from apps.ai.constants import CallStatus, Provider, Purpose
from apps.core.models import TimeStampedModel


class AiCall(TimeStampedModel):
    """One request to an AI provider: what was sent, what came back, its tokens and the cost at the prices of that
    moment. Image bytes are not stored, only their type and size."""

    provider = models.CharField(max_length=16, choices=Provider.choices)
    model = models.CharField(max_length=100)
    purpose = models.CharField(max_length=32, choices=Purpose.choices, default=Purpose.OTHER)
    status = models.CharField(max_length=16, choices=CallStatus.choices, default=CallStatus.OK)
    error = models.TextField(blank=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="ai_calls"
    )
    account = models.ForeignKey(
        "integrations.SocialAccount", on_delete=models.SET_NULL, null=True, blank=True, related_name="ai_calls"
    )
    # What the call was about: a draft, a comment, a post or an inbox reply.
    subject_type = models.ForeignKey(ContentType, on_delete=models.SET_NULL, null=True, blank=True)
    subject_id = models.PositiveBigIntegerField(null=True, blank=True)
    subject = GenericForeignKey("subject_type", "subject_id")
    system = models.TextField(blank=True)
    request = models.JSONField(default=dict)
    response = models.JSONField(null=True, blank=True)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    cost_usd = models.DecimalField(max_digits=10, decimal_places=6, default=0)
    duration_ms = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["subject_type", "subject_id"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.get_purpose_display()} ({self.model})"

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

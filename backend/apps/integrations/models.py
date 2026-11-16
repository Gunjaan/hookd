from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.fields import EncryptedTextField
from apps.core.models import TimeStampedModel
from apps.integrations.constants import Platform


class SocialAccount(TimeStampedModel):
    """A creator's connected account on one platform (Instagram, YouTube, ...)."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        EXPIRED = "expired", "Expired"
        REVOKED = "revoked", "Revoked"
        ERROR = "error", "Error"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="social_accounts")
    platform = models.CharField(max_length=32, choices=Platform.choices)
    external_id = models.CharField(max_length=128, help_text="Account id on the platform, as used in webhooks.")
    username = models.CharField(max_length=255, blank=True)
    display_name = models.CharField(max_length=255, blank=True)
    avatar_url = models.URLField(max_length=2000, blank=True)
    access_token = EncryptedTextField()
    refresh_token = EncryptedTextField(blank=True, default="")
    token_expires_at = models.DateTimeField(null=True, blank=True)
    scopes = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    status_detail = models.CharField(max_length=500, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    last_refreshed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["platform", "username"]
        constraints = [
            models.UniqueConstraint(fields=["platform", "external_id"], name="uniq_social_account_platform_external"),
        ]

    def __str__(self) -> str:
        return f"{self.get_platform_display()} @{self.username or self.external_id}"

    def token_expires_within(self, window: timedelta) -> bool:
        return self.token_expires_at is not None and self.token_expires_at <= timezone.now() + window

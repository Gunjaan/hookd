from django.db import models

from apps.core.models import TimeStampedModel
from apps.integrations.models import SocialAccount


class Comment(TimeStampedModel):
    """A comment on one of a connected account's posts, received from a platform webhook."""

    account = models.ForeignKey(SocialAccount, on_delete=models.CASCADE, related_name="comments")
    external_id = models.CharField(max_length=128, help_text="Comment id on the platform.")
    media_external_id = models.CharField(max_length=128, blank=True)
    parent_external_id = models.CharField(max_length=128, blank=True, help_text="Set when this is a reply.")
    author_external_id = models.CharField(max_length=128, blank=True)
    author_username = models.CharField(max_length=255, blank=True)
    text = models.TextField(blank=True)
    is_from_account = models.BooleanField(default=False, help_text="Written by the connected account itself.")
    commented_at = models.DateTimeField(null=True, blank=True)
    payload = models.JSONField(default=dict, blank=True, help_text="Raw webhook value, for debugging.")

    class Meta:
        ordering = ["-commented_at", "-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["account", "external_id"], name="uniq_comment_account_external"),
        ]

    def __str__(self) -> str:
        return f"@{self.author_username or self.author_external_id}: {self.text[:50]}"

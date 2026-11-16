import uuid

from django.db import models

from apps.core.models import TimeStampedModel
from apps.integrations.constants import MediaType, ProductType, PublishKind
from apps.integrations.models import SocialAccount


class Post(TimeStampedModel):
    """A post, reel or story on a connected account, cached from the platform."""

    account = models.ForeignKey(SocialAccount, on_delete=models.CASCADE, related_name="posts")
    external_id = models.CharField(max_length=128, help_text="Media id on the platform.")
    media_type = models.CharField(max_length=16, choices=MediaType.choices, default=MediaType.IMAGE)
    product_type = models.CharField(max_length=16, choices=ProductType.choices, default=ProductType.FEED)
    caption = models.TextField(blank=True)
    permalink = models.URLField(max_length=500, blank=True)
    media_url = models.TextField(blank=True, help_text="Platform CDN URL. Expires; refreshed on every sync.")
    thumbnail_url = models.TextField(blank=True)
    posted_at = models.DateTimeField(null=True, blank=True)
    like_count = models.PositiveIntegerField(null=True, blank=True)
    comments_count = models.PositiveIntegerField(null=True, blank=True)
    insights = models.JSONField(default=list, blank=True, help_text="List of {key, label, value, unit}.")
    insights_fetched_at = models.DateTimeField(null=True, blank=True)
    synced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-posted_at", "-pk"]
        constraints = [
            models.UniqueConstraint(fields=["account", "external_id"], name="uniq_post_account_external"),
        ]

    def __str__(self) -> str:
        return f"{self.account} {self.external_id}"

    @property
    def preview_url(self) -> str:
        """Image to show in the UI: the video thumbnail for videos, otherwise the media itself."""
        return self.thumbnail_url or (self.media_url if self.media_type != MediaType.VIDEO else "")


class PublishJob(TimeStampedModel):
    """A post the creator is publishing from Hookd. Uploads live in PublishAsset until it goes live."""

    class Status(models.TextChoices):
        PROCESSING = "processing", "Processing"
        PUBLISHED = "published", "Published"
        FAILED = "failed", "Failed"

    account = models.ForeignKey(SocialAccount, on_delete=models.CASCADE, related_name="publish_jobs")
    kind = models.CharField(max_length=16, choices=PublishKind.choices)
    caption = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PROCESSING)
    publish_id = models.CharField(max_length=128, blank=True, help_text="Platform container id to poll.")
    error = models.TextField(blank=True)
    post = models.ForeignKey(Post, on_delete=models.SET_NULL, null=True, blank=True, related_name="publish_jobs")
    published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} for {self.account} ({self.status})"


def asset_upload_path(asset: "PublishAsset", filename: str) -> str:
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"
    return f"publish/{asset.token}.{extension}"


class PublishAsset(TimeStampedModel):
    """One uploaded file of a PublishJob, served at an unguessable public URL so the platform can fetch it."""

    job = models.ForeignKey(PublishJob, on_delete=models.CASCADE, related_name="assets")
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    position = models.PositiveSmallIntegerField(default=0)
    file = models.FileField(upload_to=asset_upload_path)
    content_type = models.CharField(max_length=64)

    class Meta:
        ordering = ["position"]

    def __str__(self) -> str:
        return self.file.name

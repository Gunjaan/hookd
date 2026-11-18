import uuid

from django.conf import settings
from django.db import models

from apps.content.models import PublishJob
from apps.core.models import TimeStampedModel


class Look(models.TextChoices):
    MODEL = "model", "On a model"
    STUDIO = "studio", "Clean studio"
    LIFESTYLE = "lifestyle", "Lifestyle scene"
    FLATLAY = "flatlay", "Flat lay"


class FormatChoice(models.TextChoices):
    AUTO = "auto", "Let Hookd decide"
    SINGLE = "single", "One photo"
    CAROUSEL = "carousel", "Carousel"


class Creative(TimeStampedModel):
    """A post draft Hookd makes from the seller's product photos: images plus caption and hashtags."""

    class Status(models.TextChoices):
        PLANNING = "planning", "Planning"
        GENERATING = "generating", "Making photos"
        READY = "ready", "Ready to post"
        FAILED = "failed", "Failed"
        PUBLISHED = "published", "Posted"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="creatives")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PLANNING)
    look = models.CharField(max_length=16, choices=Look.choices, default=Look.MODEL)
    format_choice = models.CharField(max_length=16, choices=FormatChoice.choices, default=FormatChoice.AUTO)
    notes = models.TextField(blank=True, help_text="What the seller asked for.")
    brief = models.JSONField(default=dict, blank=True, help_text="Product, branding and audience from the copy engine.")
    caption = models.TextField(blank=True)
    hashtags = models.TextField(blank=True)
    brain_questions = models.JSONField(
        default=list, blank=True,
        help_text="What the reply agent needs to know: list of {label, question, scope, answer}. scope is post or account.",
    )
    brain_saved_at = models.DateTimeField(null=True, blank=True, help_text="When the answers went into the agent's brains.")
    error = models.TextField(blank=True)
    publish_job = models.ForeignKey(
        PublishJob, on_delete=models.SET_NULL, null=True, blank=True, related_name="creatives"
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Creative {self.pk} ({self.status})"

    @property
    def is_working(self) -> bool:
        return self.status in (self.Status.PLANNING, self.Status.GENERATING)

    @property
    def answered_questions(self) -> list[dict]:
        return [item for item in self.brain_questions if item.get("answer")]


def source_upload_path(source: "CreativeSource", filename: str) -> str:
    return f"creatives/sources/{source.token}.jpg"


def slide_upload_path(slide: "CreativeSlide", filename: str) -> str:
    return f"creatives/slides/{uuid.uuid4()}.jpg"


class CreativeSource(TimeStampedModel):
    """One product photo the seller uploaded, stored downscaled as JPEG."""

    creative = models.ForeignKey(Creative, on_delete=models.CASCADE, related_name="sources")
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    position = models.PositiveSmallIntegerField(default=0)
    file = models.FileField(upload_to=source_upload_path)

    class Meta:
        ordering = ["position"]


class CreativeSlide(TimeStampedModel):
    """One generated image of the post (the only one for a single post, one of several for a carousel)."""

    class Status(models.TextChoices):
        PENDING = "pending", "Making"
        READY = "ready", "Ready"
        FAILED = "failed", "Failed"

    creative = models.ForeignKey(Creative, on_delete=models.CASCADE, related_name="slides")
    position = models.PositiveSmallIntegerField(default=0)
    role = models.CharField(max_length=64, blank=True, help_text="What the shot is for, for example hero or detail.")
    prompt = models.TextField(help_text="Shot description from the copy engine.")
    tweak = models.CharField(max_length=300, blank=True, help_text="The seller's last change request.")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    image = models.FileField(upload_to=slide_upload_path, blank=True)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ["position"]


class CreativeSlideVersion(TimeStampedModel):
    """An earlier image of a slide, kept when the seller redoes or swaps it. It can be put back at no AI cost."""

    slide = models.ForeignKey(CreativeSlide, on_delete=models.CASCADE, related_name="versions")
    image = models.FileField(upload_to=slide_upload_path)
    tweak = models.CharField(max_length=300, blank=True, help_text="The change request that made this image.")

    class Meta:
        ordering = ["-created_at", "-pk"]

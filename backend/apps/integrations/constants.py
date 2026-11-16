from enum import StrEnum

from django.db import models


class Platform(models.TextChoices):
    INSTAGRAM = "instagram", "Instagram"
    YOUTUBE = "youtube", "YouTube"


class Capability(StrEnum):
    """Features a provider may support. The UI and services branch on these, never on platform names."""

    PUBLIC_REPLY = "public_reply"
    PRIVATE_REPLY = "private_reply"
    DIRECT_MESSAGE = "direct_message"
    HIDE_COMMENT = "hide_comment"
    PUBLISH = "publish"
    REALTIME_WEBHOOKS = "realtime_webhooks"
    INSIGHTS = "insights"


class MediaType(models.TextChoices):
    IMAGE = "image", "Image"
    VIDEO = "video", "Video"
    CAROUSEL = "carousel", "Carousel"


class ProductType(models.TextChoices):
    FEED = "feed", "Post"
    REEL = "reel", "Reel"
    STORY = "story", "Story"


class PublishKind(models.TextChoices):
    """What a creator can publish from Hookd."""

    IMAGE = "image", "Image"
    REEL = "reel", "Reel"
    CAROUSEL = "carousel", "Carousel"


class PublishState(StrEnum):
    IN_PROGRESS = "in_progress"
    FINISHED = "finished"
    FAILED = "failed"

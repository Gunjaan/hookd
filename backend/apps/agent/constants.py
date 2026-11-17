from django.db import models


class ReplyStatus(models.TextChoices):
    PENDING_REVIEW = "pending_review", "Needs review"
    QUEUED = "queued", "Scheduled"
    SENDING = "sending", "Sending"
    SENT = "sent", "Sent"
    SKIPPED = "skipped", "Skipped"
    REJECTED = "rejected", "Rejected"
    FAILED = "failed", "Failed"


class FactSource(models.TextChoices):
    """Where the agent learned a fact on its own."""

    CAPTION = "caption", "From a caption"
    REPLY = "reply", "From your reply"


class FactScope(models.TextChoices):
    POST = "post", "This post"
    ACCOUNT = "account", "All posts"

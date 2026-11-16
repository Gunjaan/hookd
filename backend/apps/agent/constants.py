from django.db import models


class RuleAction(models.TextChoices):
    AUTO_REPLY = "auto_reply", "Reply automatically"
    REVIEW = "review", "Draft a reply for my review"
    IGNORE = "ignore", "Ignore"


class FallbackAction(models.TextChoices):
    REVIEW = "review", "Draft a reply for my review"
    IGNORE = "ignore", "Ignore"


class ReplyStatus(models.TextChoices):
    PENDING_REVIEW = "pending_review", "Needs review"
    QUEUED = "queued", "Scheduled"
    SENDING = "sending", "Sending"
    SENT = "sent", "Sent"
    SKIPPED = "skipped", "Skipped"
    REJECTED = "rejected", "Rejected"
    FAILED = "failed", "Failed"

from django.db import models


class RuleAction(models.TextChoices):
    AUTO_REPLY = "auto_reply", "Reply on its own"
    REVIEW = "review", "Draft a reply for me to approve"
    IGNORE = "ignore", "Don't reply"


class FallbackAction(models.TextChoices):
    REVIEW = "review", "Draft a reply for me to approve"
    IGNORE = "ignore", "Don't reply"


class ReplyStatus(models.TextChoices):
    PENDING_REVIEW = "pending_review", "Needs review"
    QUEUED = "queued", "Scheduled"
    SENDING = "sending", "Sending"
    SENT = "sent", "Sent"
    SKIPPED = "skipped", "Skipped"
    REJECTED = "rejected", "Rejected"
    FAILED = "failed", "Failed"

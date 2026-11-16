from django.db import models


class ReplyStatus(models.TextChoices):
    PENDING_REVIEW = "pending_review", "Needs review"
    QUEUED = "queued", "Scheduled"
    SENDING = "sending", "Sending"
    SENT = "sent", "Sent"
    SKIPPED = "skipped", "Skipped"
    REJECTED = "rejected", "Rejected"
    FAILED = "failed", "Failed"

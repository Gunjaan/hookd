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

    POST = "post", "From the post"
    REPLY = "reply", "From your reply"
    AGENT = "agent", "Saved by Hooky"


class FactScope(models.TextChoices):
    POST = "post", "This post"
    ACCOUNT = "account", "All posts"


class RunStatus(models.TextChoices):
    """Where an agent run stands. Only `failed` runs never wake up again."""

    QUEUED = "queued", "Starting"
    RUNNING = "running", "Working"
    WAITING_SELLER = "waiting_seller", "Waiting for your answer"
    WAITING_FOLLOWUP = "waiting_followup", "Follow-up scheduled"
    DONE = "done", "Done"
    HANDED_OFF = "handed_off", "Handed to you"
    FAILED = "failed", "Failed"


# Runs that never wake up again: their pending events are dropped.
FINISHED_RUN_STATUSES = (RunStatus.HANDED_OFF, RunStatus.FAILED)


class EventKind(models.TextChoices):
    """What woke a run up."""

    COMMENT = "comment", "New comment"
    SELLER_ANSWER = "seller_answer", "Your answer"
    DM = "dm", "Buyer DM"
    FOLLOWUP = "followup", "Follow-up due"


class QuestionStatus(models.TextChoices):
    OPEN = "open", "Waiting for you"
    ANSWERED = "answered", "Answered"
    DISMISSED = "dismissed", "Dismissed"
    EXPIRED = "expired", "Not answered in time"


class DMKind(models.TextChoices):
    PRIVATE_REPLY = "private_reply", "Private reply to the comment"
    DM = "dm", "Direct message"


class TicketStatus(models.TextChoices):
    OPEN = "open", "Open"
    IN_PROGRESS = "in_progress", "In progress"
    RESOLVED = "resolved", "Resolved"

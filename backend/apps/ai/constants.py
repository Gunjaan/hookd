from django.db import models


class Provider(models.TextChoices):
    CLAUDE = "claude", "Claude"
    GEMINI = "gemini", "Gemini"


class Purpose(models.TextChoices):
    """What an AI call was made for. Every call site wraps its engine call in `ai.services.attributed`."""

    CREATIVE_PLAN = "creative_plan", "Post studio: plan and copy"
    CREATIVE_PHOTO = "creative_photo", "Post studio: photo"
    CAPTION_REWRITE = "caption_rewrite", "Post studio: caption rewrite"
    COMMENT_REPLY = "comment_reply", "Agent: comment reply (single call, before the agent loop)"
    COMMENT_AGENT = "comment_agent", "Agent: comment agent step"
    REPLY_PREVIEW = "reply_preview", "Agent: reply preview"
    LEARN_CAPTION = "learn_caption", "Agent: learn from caption"
    LEARN_REPLY = "learn_reply", "Agent: learn from reply"
    OTHER = "other", "Other"


class CallStatus(models.TextChoices):
    OK = "ok", "OK"
    FAILED = "failed", "Failed"

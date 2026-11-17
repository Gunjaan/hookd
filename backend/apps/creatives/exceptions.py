from apps.core.exceptions import HookdError


class CreativeNotFound(HookdError):
    default_message = "Draft not found."


class CreativeNotReady(HookdError):
    default_message = "This draft is not ready yet."


class CreativeEngineError(HookdError):
    """`usage` is set when the model answered (and billed) but the answer was unusable."""

    default_message = "The AI could not finish this. Please try again."

    def __init__(self, message: str | None = None, usage=None):
        super().__init__(message)
        self.usage = usage


class CreativeDailyLimitReached(HookdError):
    default_message = "You have reached today's limit of new drafts. Try again tomorrow."


class CreativeCaptionTooLong(HookdError):
    default_message = "Caption and hashtags together must be under 2200 characters."


class SlideNotFound(HookdError):
    default_message = "Photo not found."


class SlideVersionNotFound(HookdError):
    default_message = "That earlier photo was not found."

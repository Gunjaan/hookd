from apps.core.exceptions import HookdError


class CreativeNotFound(HookdError):
    default_message = "Draft not found."


class CreativeNotReady(HookdError):
    default_message = "This draft is not ready yet."


class CreativeEngineError(HookdError):
    default_message = "The AI could not finish this. Please try again."


class CreativeDailyLimitReached(HookdError):
    default_message = "You have reached today's limit of new drafts. Try again tomorrow."


class CreativeCaptionTooLong(HookdError):
    default_message = "Caption and hashtags together must be under 2200 characters."


class SlideNotFound(HookdError):
    default_message = "Photo not found."

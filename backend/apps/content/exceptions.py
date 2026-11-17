from apps.core.exceptions import HookdError


class PostNotFound(HookdError):
    default_message = "Post not found."


class PostRemoved(HookdError):
    default_message = "This post is no longer on the platform (deleted or archived)."


class PublishJobNotFound(HookdError):
    default_message = "Publish request not found."


class PublishingUnavailable(HookdError):
    default_message = "Publishing needs APP_URL to be a public HTTPS address so the platform can download your media."


class PublishQuotaReached(HookdError):
    default_message = "This account reached its publishing limit for the last 24 hours. Try again later."

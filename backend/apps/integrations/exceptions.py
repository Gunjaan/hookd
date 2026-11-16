from apps.core.exceptions import HookdError


class ProviderNotFound(HookdError):
    default_message = "This platform is not supported."


class ProviderNotConfigured(HookdError):
    default_message = "This platform has not been configured on the server."


class OAuthStateError(HookdError):
    default_message = "The connection request expired or is invalid. Please try again."


class AccountAlreadyLinked(HookdError):
    default_message = "This account is already connected to another Hookd user."


class AccountNotFound(HookdError):
    default_message = "Connected account not found."


class ProviderAPIError(HookdError):
    """The remote platform returned an error or could not be reached."""

    default_message = "The platform returned an error."


class TokenExpiredError(ProviderAPIError):
    default_message = "The platform session expired. Please reconnect the account."


class RateLimitedError(ProviderAPIError):
    """The platform asked us to slow down or temporarily blocked the action."""

    default_message = "The platform asked Hookd to slow down. Sending is paused for a while."


class WebhooksNotSupported(HookdError):
    default_message = "This platform does not support webhooks."


class WebhookVerificationError(HookdError):
    default_message = "Webhook verification failed."


class FeatureNotSupported(HookdError):
    default_message = "This platform does not support that feature yet."

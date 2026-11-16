from apps.core.exceptions import HookdError


class ReplyNotFound(HookdError):
    default_message = "Reply not found."


class RuleNotFound(HookdError):
    default_message = "Rule not found."


class ReplyNotEditable(HookdError):
    default_message = "This reply can no longer be changed."


class ReplyTextRejected(HookdError):
    default_message = "The platform would reject this reply."

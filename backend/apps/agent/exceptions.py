from apps.core.exceptions import HookdError


class ReplyNotFound(HookdError):
    default_message = "Reply not found."


class ReplyNotEditable(HookdError):
    default_message = "This reply can no longer be changed."


class ReplyTextRejected(HookdError):
    default_message = "The platform would reject this reply."


class LearnedFactNotFound(HookdError):
    default_message = "Fact not found."


class QuestionNotFound(HookdError):
    default_message = "Question not found."


class QuestionNotOpen(HookdError):
    default_message = "This question was already answered."


class TicketNotFound(HookdError):
    default_message = "Ticket not found."

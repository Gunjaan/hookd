"""Base class for expected, user facing domain errors.

Views catch HookdError and show `message` to the user.
"""


class HookdError(Exception):
    default_message = "Something went wrong."

    def __init__(self, message: str | None = None):
        self.message = message or self.default_message
        super().__init__(self.message)

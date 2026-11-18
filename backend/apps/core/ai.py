"""Provider neutral results of an AI call, shared by the Claude and Gemini clients and the engines."""

from dataclasses import dataclass


@dataclass(frozen=True)
class TokenUsage:
    """Billed tokens of one call. Output includes thinking tokens, which are billed as output."""

    model: str
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class JsonAnswer:
    data: dict
    usage: TokenUsage

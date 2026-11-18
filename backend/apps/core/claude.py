"""Shared Claude (Anthropic API) access for the text engines: one place for the client, timeouts and errors."""

import base64
import json
import logging

import anthropic
from django.conf import settings

from apps.core.ai import JsonAnswer, TokenUsage
from apps.core.exceptions import HookdError

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 90


class ClaudeUnavailable(HookdError):
    default_message = "AI is not set up yet: ANTHROPIC_API_KEY is missing."


class ClaudeError(HookdError):
    default_message = "The AI service did not answer. Please try again."


def image_block(data: bytes, mime_type: str) -> dict:
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": mime_type, "data": base64.standard_b64encode(data).decode("ascii")},
    }


def token_usage(model: str, response) -> TokenUsage:
    usage = response.usage
    return TokenUsage(
        model=model,
        input_tokens=usage.input_tokens + (usage.cache_read_input_tokens or 0) + (usage.cache_creation_input_tokens or 0),
        output_tokens=usage.output_tokens,
    )


def generate_json(model: str, content: str | list, *, system: str, schema: dict, max_output_tokens: int) -> JsonAnswer:
    """One call with a JSON schema answer and its token usage. Raises ClaudeError when there is no usable answer."""
    if not settings.ANTHROPIC_API_KEY:
        raise ClaudeUnavailable()
    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY, timeout=TIMEOUT_SECONDS)
    try:
        response = client.messages.create(
            model=model,
            max_tokens=max_output_tokens,
            system=system,
            messages=[{"role": "user", "content": content}],
            # Low effort keeps the always on thinking short: these are simple, high volume calls.
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
        )
    except anthropic.APIStatusError as exc:
        logger.warning("Claude %s failed: %s", model, exc)
        raise ClaudeError(f"The AI service refused the request ({exc.status_code}). Please try again.") from exc
    except anthropic.APIConnectionError as exc:
        logger.warning("Claude %s unreachable: %s", model, exc)
        raise ClaudeError("The AI service did not answer in time. Please try again.") from exc

    if response.stop_reason == "refusal":
        raise ClaudeError("The AI declined this request. Try different wording.")
    if response.stop_reason == "max_tokens":
        raise ClaudeError("The AI answer was cut off. Please try again.")
    text = next((block.text for block in response.content if block.type == "text"), "")
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ClaudeError("The AI returned an unreadable answer. Please try again.") from exc
    if not isinstance(data, dict):
        raise ClaudeError("The AI returned an unreadable answer. Please try again.")
    return JsonAnswer(data=data, usage=token_usage(model, response))

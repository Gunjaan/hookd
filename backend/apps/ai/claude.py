"""Shared Claude (Anthropic API) access for the text engines: one place for the client, timeouts, errors and the call
record (`ai.services.record`)."""

import base64
import json
import logging
import time

import anthropic
from django.conf import settings

from apps.ai import services
from apps.ai.constants import Provider
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


def _stored_content(content: str | list) -> str | list:
    """The sent content with every image replaced by its type and size."""
    if isinstance(content, str):
        return content
    stored = []
    for block in content:
        if block.get("type") == "image":
            source = block["source"]
            stored.append(services.media_summary(source["media_type"], base64.standard_b64decode(source["data"])))
        else:
            stored.append(block)
    return stored


def _tokens(response) -> tuple[int, int]:
    usage = response.usage
    input_tokens = usage.input_tokens + (usage.cache_read_input_tokens or 0) + (usage.cache_creation_input_tokens or 0)
    return input_tokens, usage.output_tokens


def _answer(response) -> dict:
    """The JSON answer of a finished call. Raises ClaudeError when there is none."""
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
    return data


def generate_json(model: str, content: str | list, *, system: str, schema: dict, max_output_tokens: int) -> dict:
    """One call with a JSON schema answer. Raises ClaudeError when there is no usable answer. Every request that
    reaches the API is recorded, failed ones too."""
    if not settings.ANTHROPIC_API_KEY:
        raise ClaudeUnavailable()
    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY, timeout=TIMEOUT_SECONDS)
    request = {"content": _stored_content(content), "max_output_tokens": max_output_tokens}
    started = time.monotonic()

    def record(response=None, error: str = "") -> None:
        input_tokens, output_tokens = _tokens(response) if response is not None else (0, 0)
        stored = None
        if response is not None:
            stored = {
                "stop_reason": response.stop_reason,
                "text": next((block.text for block in response.content if block.type == "text"), ""),
            }
        services.record(
            provider=Provider.CLAUDE, model=model, system=system, request=request, response=stored,
            input_tokens=input_tokens, output_tokens=output_tokens,
            duration_ms=int((time.monotonic() - started) * 1000), error=error,
        )

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
        record(error=str(exc))
        raise ClaudeError(f"The AI service refused the request ({exc.status_code}). Please try again.") from exc
    except anthropic.APIConnectionError as exc:
        logger.warning("Claude %s unreachable: %s", model, exc)
        record(error=str(exc))
        raise ClaudeError("The AI service did not answer in time. Please try again.") from exc

    try:
        data = _answer(response)
    except ClaudeError as exc:
        record(response, error=exc.message)
        raise
    record(response)
    return data

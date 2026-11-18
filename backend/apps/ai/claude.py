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
    try:
        data = json.loads(_response_text(response))
    except ValueError as exc:
        raise ClaudeError("The AI returned an unreadable answer. Please try again.") from exc
    if not isinstance(data, dict):
        raise ClaudeError("The AI returned an unreadable answer. Please try again.")
    return data


def _client():
    if not settings.ANTHROPIC_API_KEY:
        raise ClaudeUnavailable()
    return anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY, timeout=TIMEOUT_SECONDS)


def _response_text(response) -> str:
    return next((block.text for block in response.content if block.type == "text"), "")


def _call(model: str, *, system: str, request: dict, stored_response, **create_kwargs):
    """Send one request. Returns the response and a `record(response, error="")` the caller calls once it knows
    whether the answer is usable; API and network errors are recorded here and raise ClaudeError.
    `stored_response(response)` is what the record keeps of a response."""
    client = _client()
    started = time.monotonic()

    def record(response=None, error: str = "") -> None:
        input_tokens, output_tokens = _tokens(response) if response is not None else (0, 0)
        services.record(
            provider=Provider.CLAUDE, model=model, system=system, request=request,
            response=stored_response(response) if response is not None else None,
            input_tokens=input_tokens, output_tokens=output_tokens,
            duration_ms=int((time.monotonic() - started) * 1000), error=error,
        )

    try:
        response = client.messages.create(model=model, system=system, **create_kwargs)
    except anthropic.APIStatusError as exc:
        logger.warning("Claude %s failed: %s", model, exc)
        record(error=str(exc))
        raise ClaudeError(f"The AI service refused the request ({exc.status_code}). Please try again.") from exc
    except anthropic.APIConnectionError as exc:
        logger.warning("Claude %s unreachable: %s", model, exc)
        record(error=str(exc))
        raise ClaudeError("The AI service did not answer in time. Please try again.") from exc
    return response, record


def generate_json(model: str, content: str | list, *, system: str, schema: dict, max_output_tokens: int) -> dict:
    """One call with a JSON schema answer. Raises ClaudeError when there is no usable answer. Every request that
    reaches the API is recorded, failed ones too."""
    response, record = _call(
        model,
        system=system,
        request={"content": _stored_content(content), "max_output_tokens": max_output_tokens},
        stored_response=lambda response: {"stop_reason": response.stop_reason, "text": _response_text(response)},
        max_tokens=max_output_tokens,
        messages=[{"role": "user", "content": content}],
        # Low effort keeps the always on thinking short: these are simple, high volume calls.
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
    )
    try:
        data = _answer(response)
    except ClaudeError as exc:
        record(response, error=exc.message)
        raise
    record(response)
    return data


def run_step(model: str, *, system: str, tools: list[dict], messages: list[dict], max_output_tokens: int) -> tuple[list[dict], str]:
    """One turn of a tool use loop. Returns the assistant content blocks (plain dicts, thinking blocks included, to be
    sent back unchanged) and the stop reason. Refusals and cut off turns raise ClaudeError. Every request is
    recorded with the transcript it was sent."""
    response, record = _call(
        model,
        system=system,
        request={"messages": messages, "tools": [tool["name"] for tool in tools], "max_output_tokens": max_output_tokens},
        stored_response=lambda response: {"stop_reason": response.stop_reason, "content": response.to_dict()["content"]},
        max_tokens=max_output_tokens,
        tools=tools,
        tool_choice={"type": "auto"},
        messages=messages,
        # The system prompt and tools never change, so caching makes every step after the first cheap.
        cache_control={"type": "ephemeral"},
        output_config={"effort": "low"},
    )
    if response.stop_reason in ("refusal", "max_tokens"):
        error = "The AI declined this request." if response.stop_reason == "refusal" else "The AI answer was cut off."
        record(response, error=error)
        raise ClaudeError(error)
    record(response)
    return response.to_dict()["content"], response.stop_reason

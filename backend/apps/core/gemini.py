"""Shared Gemini (Google AI Studio) access for the AI engines: one place for the client, timeouts and errors."""

import json
import logging
from dataclasses import dataclass

import httpx2
from django.conf import settings
from google import genai
from google.genai import errors, types

from apps.core.exceptions import HookdError

logger = logging.getLogger(__name__)

TIMEOUT_MS = 90_000


class GeminiUnavailable(HookdError):
    default_message = "AI is not set up yet: GEMINI_API_KEY is missing."


class GeminiError(HookdError):
    default_message = "The AI service did not answer. Please try again."


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


def token_usage(model: str, response: types.GenerateContentResponse) -> TokenUsage:
    meta = response.usage_metadata
    if meta is None:
        return TokenUsage(model=model)
    return TokenUsage(
        model=model,
        input_tokens=meta.prompt_token_count or 0,
        output_tokens=(meta.candidates_token_count or 0) + (meta.thoughts_token_count or 0),
    )


def generate(model: str, contents: list, config: types.GenerateContentConfig) -> types.GenerateContentResponse:
    if not settings.GEMINI_API_KEY:
        raise GeminiUnavailable()
    # Keep the client referenced for the whole call: a temporary one is closed as soon as it is garbage collected.
    client = genai.Client(api_key=settings.GEMINI_API_KEY, http_options=types.HttpOptions(timeout=TIMEOUT_MS))
    try:
        return client.models.generate_content(model=model, contents=contents, config=config)
    except errors.APIError as exc:
        logger.warning("Gemini %s failed: %s", model, exc)
        raise GeminiError(f"The AI service refused the request ({exc.code}). Please try again.") from exc
    except httpx2.HTTPError as exc:
        logger.warning("Gemini %s unreachable: %s", model, exc)
        raise GeminiError("The AI service did not answer in time. Please try again.") from exc


def generate_json(model: str, contents: list, *, system: str, schema: dict, max_output_tokens: int) -> JsonAnswer:
    """One call with a JSON schema answer and its token usage. Raises GeminiError when the answer is not JSON."""
    response = generate(
        model,
        contents,
        types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_json_schema=schema,
            max_output_tokens=max_output_tokens,
        ),
    )
    try:
        data = json.loads(response.text or "")
    except ValueError as exc:
        raise GeminiError("The AI returned an unreadable answer. Please try again.") from exc
    if not isinstance(data, dict):
        raise GeminiError("The AI returned an unreadable answer. Please try again.")
    return JsonAnswer(data=data, usage=token_usage(model, response))

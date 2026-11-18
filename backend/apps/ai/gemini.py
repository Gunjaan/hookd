"""Shared Gemini (Google AI Studio) access for the image engine: one place for the client, timeouts, errors and the
call record (`ai.services.record`)."""

import logging
import time

import httpx2
from django.conf import settings
from google import genai
from google.genai import errors, types

from apps.ai import services
from apps.ai.constants import Provider
from apps.core.exceptions import HookdError

logger = logging.getLogger(__name__)

TIMEOUT_MS = 90_000


class GeminiUnavailable(HookdError):
    default_message = "AI photos are not set up yet: GEMINI_API_KEY is missing."


class GeminiError(HookdError):
    default_message = "The AI service did not answer. Please try again."


def _tokens(response: types.GenerateContentResponse) -> tuple[int, int]:
    """Billed tokens. Output includes thinking tokens, which are billed as output."""
    meta = response.usage_metadata
    if meta is None:
        return 0, 0
    return meta.prompt_token_count or 0, (meta.candidates_token_count or 0) + (meta.thoughts_token_count or 0)


def _stored_part(part) -> dict | str:
    """A sent or received part as stored: text as is, an image as its type and size."""
    if isinstance(part, str):
        return part
    if part.inline_data and part.inline_data.data:
        return services.media_summary(part.inline_data.mime_type or "", part.inline_data.data)
    return part.text or ""


def generate(model: str, contents: list, config: types.GenerateContentConfig) -> types.GenerateContentResponse:
    """One call. Every request that reaches the API is recorded, failed ones too."""
    if not settings.GEMINI_API_KEY:
        raise GeminiUnavailable()
    # Keep the client referenced for the whole call: a temporary one is closed as soon as it is garbage collected.
    client = genai.Client(api_key=settings.GEMINI_API_KEY, http_options=types.HttpOptions(timeout=TIMEOUT_MS))
    request = {
        "contents": [_stored_part(part) for part in contents],
        "config": config.model_dump(mode="json", exclude_none=True),
    }
    started = time.monotonic()

    def record(response=None, error: str = "") -> None:
        input_tokens, output_tokens = _tokens(response) if response is not None else (0, 0)
        stored = {"parts": [_stored_part(part) for part in response.parts or []]} if response is not None else None
        services.record(
            provider=Provider.GEMINI, model=model, request=request, response=stored,
            input_tokens=input_tokens, output_tokens=output_tokens,
            duration_ms=int((time.monotonic() - started) * 1000), error=error,
        )

    try:
        response = client.models.generate_content(model=model, contents=contents, config=config)
    except errors.APIError as exc:
        logger.warning("Gemini %s failed: %s", model, exc)
        record(error=str(exc))
        raise GeminiError(f"The AI service refused the request ({exc.code}). Please try again.") from exc
    except httpx2.HTTPError as exc:
        logger.warning("Gemini %s unreachable: %s", model, exc)
        record(error=str(exc))
        raise GeminiError("The AI service did not answer in time. Please try again.") from exc
    record(response)
    return response

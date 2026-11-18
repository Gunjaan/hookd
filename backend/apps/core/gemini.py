"""Shared Gemini (Google AI Studio) access for the image engine: one place for the client, timeouts and errors."""

import logging

import httpx2
from django.conf import settings
from google import genai
from google.genai import errors, types

from apps.core.ai import TokenUsage
from apps.core.exceptions import HookdError

logger = logging.getLogger(__name__)

TIMEOUT_MS = 90_000


class GeminiUnavailable(HookdError):
    default_message = "AI photos are not set up yet: GEMINI_API_KEY is missing."


class GeminiError(HookdError):
    default_message = "The AI service did not answer. Please try again."


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

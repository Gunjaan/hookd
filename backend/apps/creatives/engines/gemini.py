"""Gemini (Google AI Studio) image engine: renders each post photo from the product photos and a prompt."""

from django.conf import settings
from google.genai import types

from apps.ai.gemini import generate
from apps.creatives.engines.base import ImageEngine, ProductPhoto, RenderedImage
from apps.creatives.exceptions import CreativeEngineError

SAFETY_SETTINGS = [
    types.SafetySetting(category=category, threshold=types.HarmBlockThreshold.BLOCK_LOW_AND_ABOVE)
    for category in (
        types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
        types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
        types.HarmCategory.HARM_CATEGORY_HARASSMENT,
        types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
    )
]

BLOCKED_FINISH_REASONS = {
    types.FinishReason.SAFETY,
    types.FinishReason.PROHIBITED_CONTENT,
    types.FinishReason.BLOCKLIST,
    types.FinishReason.SPII,
    types.FinishReason.IMAGE_SAFETY,
    types.FinishReason.IMAGE_PROHIBITED_CONTENT,
}

BLOCKED_MESSAGE = "This photo was blocked by the safety filter. Change the request or the product photos."


def _blocked(response: types.GenerateContentResponse) -> bool:
    if response.prompt_feedback and response.prompt_feedback.block_reason:
        return True
    return any(candidate.finish_reason in BLOCKED_FINISH_REASONS for candidate in response.candidates or [])


class GeminiImageEngine(ImageEngine):
    def render(self, photos: list[ProductPhoto], prompt: str) -> RenderedImage:
        response = generate(
            settings.CREATIVE_IMAGE_MODEL,
            [*(types.Part.from_bytes(data=photo.data, mime_type=photo.mime_type) for photo in photos), prompt],
            types.GenerateContentConfig(
                response_modalities=["IMAGE"],
                image_config=types.ImageConfig(aspect_ratio="4:5"),
                safety_settings=SAFETY_SETTINGS,
            ),
        )
        for part in response.parts or []:
            if part.inline_data and part.inline_data.data:
                return RenderedImage(data=part.inline_data.data)
        if _blocked(response):
            raise CreativeEngineError(BLOCKED_MESSAGE)
        raise CreativeEngineError("The AI did not return a photo for this shot. Try again or change the request.")

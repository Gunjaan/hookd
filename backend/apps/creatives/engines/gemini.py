"""Gemini (Google AI Studio) image engine: renders each post photo from the product photos and a prompt."""

from django.conf import settings
from google.genai import types

from apps.core.gemini import generate, token_usage
from apps.creatives.engines.base import ImageEngine, ProductPhoto, RenderedImage
from apps.creatives.exceptions import CreativeEngineError


class GeminiImageEngine(ImageEngine):
    def render(self, photos: list[ProductPhoto], prompt: str) -> RenderedImage:
        response = generate(
            settings.CREATIVE_IMAGE_MODEL,
            [*(types.Part.from_bytes(data=photo.data, mime_type=photo.mime_type) for photo in photos), prompt],
            types.GenerateContentConfig(
                response_modalities=["IMAGE"],
                image_config=types.ImageConfig(aspect_ratio="4:5"),
            ),
        )
        usage = token_usage(settings.CREATIVE_IMAGE_MODEL, response)
        for part in response.parts or []:
            if part.inline_data and part.inline_data.data:
                return RenderedImage(data=part.inline_data.data, usage=usage)
        raise CreativeEngineError(
            "The AI did not return a photo for this shot. Try again or change the request.", usage=usage
        )

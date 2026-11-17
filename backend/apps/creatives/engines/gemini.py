"""Gemini (Google AI Studio) engines: a cheap text model plans and writes, an image model renders."""

from django.conf import settings
from google.genai import types

from apps.core.gemini import generate, generate_json, token_usage
from apps.creatives import prompts
from apps.creatives.engines.base import (
    BrainQuestion,
    CopyEngine,
    ImageEngine,
    PostBrief,
    PostCopy,
    ProductPhoto,
    RenderedImage,
    ShotPlan,
)
from apps.creatives.exceptions import CreativeEngineError

PLAN_MAX_OUTPUT_TOKENS = 2560
REWRITE_MAX_OUTPUT_TOKENS = 1024
QUESTION_SCOPES = ("post", "account")


def _photo_parts(photos: list[ProductPhoto], resolution: str | None = None) -> list[types.Part]:
    return [types.Part.from_bytes(data=photo.data, mime_type=photo.mime_type, media_resolution=resolution) for photo in photos]


def _questions(items) -> list[BrainQuestion]:
    questions = []
    for item in items or []:
        question = str(item.get("question", "")).strip()
        if question:
            scope = item.get("scope") if item.get("scope") in QUESTION_SCOPES else "post"
            label = str(item.get("label", "")).strip() or question
            questions.append(BrainQuestion(label=label, question=question, scope=scope, answer=str(item.get("answer", "")).strip()))
    return questions


class GeminiCopyEngine(CopyEngine):
    def plan(self, photos: list[ProductPhoto], request_text: str) -> PostBrief:
        # Medium resolution is enough to read labels on a 1024 px photo and costs far fewer tokens than high.
        answer = generate_json(
            settings.CREATIVE_COPY_MODEL,
            [*_photo_parts(photos, "MEDIA_RESOLUTION_MEDIUM"), request_text],
            system=prompts.PLAN_SYSTEM,
            schema=prompts.PLAN_SCHEMA,
            max_output_tokens=PLAN_MAX_OUTPUT_TOKENS,
        )
        data = answer.data
        slides = [ShotPlan(role=str(s.get("role", "")), prompt=str(s.get("prompt", ""))) for s in data.get("slides") or []]
        slides = [slide for slide in slides if slide.prompt.strip()]
        if not slides:
            raise CreativeEngineError("The AI did not plan any photos. Please try again.", usage=answer.usage)
        return PostBrief(
            product=str(data.get("product", "")),
            branding=[str(item) for item in data.get("branding") or []],
            audience=str(data.get("audience", "")),
            slides=slides,
            caption=str(data.get("caption", "")),
            hashtags=[str(tag) for tag in data.get("hashtags") or []],
            questions=_questions(data.get("questions")),
            usage=answer.usage,
        )

    def rewrite(self, request_text: str) -> PostCopy:
        answer = generate_json(
            settings.CREATIVE_COPY_MODEL,
            [request_text],
            system=prompts.REWRITE_SYSTEM,
            schema=prompts.REWRITE_SCHEMA,
            max_output_tokens=REWRITE_MAX_OUTPUT_TOKENS,
        )
        data = answer.data
        return PostCopy(
            caption=str(data.get("caption", "")),
            hashtags=[str(tag) for tag in data.get("hashtags") or []],
            usage=answer.usage,
        )


class GeminiImageEngine(ImageEngine):
    def render(self, photos: list[ProductPhoto], prompt: str) -> RenderedImage:
        response = generate(
            settings.CREATIVE_IMAGE_MODEL,
            [*_photo_parts(photos), prompt],
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

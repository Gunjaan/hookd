"""Claude copy engine: a low cost text model (Haiku by default) reads the product photos, plans the shots and writes."""

from django.conf import settings

from apps.ai.claude import generate_json, image_block
from apps.creatives import prompts
from apps.creatives.engines.base import BrainQuestion, CopyEngine, PostBrief, PostCopy, ProductPhoto, ShotPlan
from apps.creatives.exceptions import CreativeEngineError

# Ceilings, not charges: they leave room for the short thinking that comes before the JSON answer.
PLAN_MAX_OUTPUT_TOKENS = 8000
REWRITE_MAX_OUTPUT_TOKENS = 4000
MAX_SLIDES = 5
MAX_HASHTAGS = 15
MAX_QUESTIONS = 6
QUESTION_SCOPES = ("post", "account")


def _hashtags(items) -> list[str]:
    return [str(tag) for tag in items or []][:MAX_HASHTAGS]


def _questions(items) -> list[BrainQuestion]:
    questions = []
    for item in items or []:
        question = str(item.get("question", "")).strip()
        if question:
            scope = item.get("scope") if item.get("scope") in QUESTION_SCOPES else "post"
            label = str(item.get("label", "")).strip() or question
            questions.append(BrainQuestion(label=label, question=question, scope=scope, answer=str(item.get("answer", "")).strip()))
    return questions[:MAX_QUESTIONS]


class ClaudeCopyEngine(CopyEngine):
    def plan(self, photos: list[ProductPhoto], request_text: str) -> PostBrief:
        data = generate_json(
            settings.CREATIVE_COPY_MODEL,
            [*(image_block(photo.data, photo.mime_type) for photo in photos), {"type": "text", "text": request_text}],
            system=prompts.PLAN_SYSTEM,
            schema=prompts.PLAN_SCHEMA,
            max_output_tokens=PLAN_MAX_OUTPUT_TOKENS,
        )
        slides = [ShotPlan(role=str(s.get("role", "")), prompt=str(s.get("prompt", ""))) for s in data.get("slides") or []]
        slides = [slide for slide in slides if slide.prompt.strip()][:MAX_SLIDES]
        if not slides:
            raise CreativeEngineError("The AI did not plan any photos. Please try again.")
        return PostBrief(
            product=str(data.get("product", "")),
            branding=[str(item) for item in data.get("branding") or []],
            audience=str(data.get("audience", "")),
            slides=slides,
            caption=str(data.get("caption", "")),
            hashtags=_hashtags(data.get("hashtags")),
            questions=_questions(data.get("questions")),
        )

    def rewrite(self, request_text: str) -> PostCopy:
        data = generate_json(
            settings.CREATIVE_COPY_MODEL,
            request_text,
            system=prompts.REWRITE_SYSTEM,
            schema=prompts.REWRITE_SCHEMA,
            max_output_tokens=REWRITE_MAX_OUTPUT_TOKENS,
        )
        return PostCopy(caption=str(data.get("caption", "")), hashtags=_hashtags(data.get("hashtags")))

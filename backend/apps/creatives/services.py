"""Post studio: turns product photos into a ready to post draft (images, caption, hashtags).

Token budget per draft: one text call that reads the photos once and returns the brief, shot plan, caption and
hashtags together, then one image call per slide. Redoing a photo renders only that slide; rewriting the caption
sends the stored brief as text, never the photos again.
"""

import logging
import re
from datetime import timedelta

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile, UploadedFile
from django.db import transaction
from django.utils import timezone

from apps.agent import services as agent_services
from apps.content import services as content_services
from apps.core.exceptions import HookdError
from apps.core.gemini import GeminiUnavailable
from apps.creatives import imaging, prompts
from apps.creatives import model_querysets as queries
from apps.creatives.engines.base import ProductPhoto
from apps.creatives.engines.registry import get_copy_engine, get_image_engine
from apps.creatives.exceptions import (
    CreativeCaptionTooLong,
    CreativeDailyLimitReached,
    CreativeNotFound,
    CreativeNotReady,
    SlideNotFound,
)
from apps.creatives.models import Creative, CreativeSlide, FormatChoice
from apps.integrations import model_querysets as account_queries
from apps.integrations.constants import PublishKind

logger = logging.getLogger(__name__)

MAX_HASHTAGS = 30
CAPTION_MAX_LENGTH = 2200
HASHTAG_PATTERN = re.compile(r"[^\w]+", re.UNICODE)
HASHTAG_SEPARATORS = re.compile(r"[\s,]+")
# Work still running after this was lost (worker restart or task time limit) and is shown as failed.
STALE_AFTER = timedelta(minutes=5)


# Drafts


def list_creatives(user) -> list[Creative]:
    return list(queries.list_creatives_for_user(user))


def get_user_creative(*, user, creative_id: int) -> Creative:
    creative = queries.get_creative_for_user(user, creative_id)
    if creative is None:
        raise CreativeNotFound()
    return creative


def expire_stale_work(creative: Creative) -> Creative:
    """Fail planning or slides that have been running longer than STALE_AFTER, so the page never spins forever."""
    cutoff = timezone.now() - STALE_AFTER
    if creative.status == Creative.Status.PLANNING and creative.updated_at < cutoff:
        return queries.update_creative(creative, status=Creative.Status.FAILED, error="This took too long. Please try again.")
    if creative.status == Creative.Status.GENERATING:
        stale = [s for s in creative.slides.all() if s.status == CreativeSlide.Status.PENDING and s.updated_at < cutoff]
        for slide in stale:
            queries.mark_slide_failed(slide, "This photo took too long. Redo it to try again.")
        if stale:
            _settle(creative)
            creative = get_user_creative(user=creative.user, creative_id=creative.pk)
    return creative


def publish_accounts(user) -> list:
    return [account for account in account_queries.list_accounts_for_user(user) if content_services.can_publish(account)]


def start_creative(*, user, photos: list[UploadedFile], look: str, format_choice: str, notes: str) -> Creative:
    """Store the downscaled photos and queue the AI work. Returns the draft in `planning`."""
    from apps.creatives.tasks import generate_creative  # tasks.py imports this module

    if not settings.GEMINI_API_KEY:
        raise GeminiUnavailable("The post studio is not set up yet: GEMINI_API_KEY is missing.")
    since = timezone.now() - timedelta(days=1)
    if queries.count_creatives_since(user, since) >= settings.CREATIVE_DAILY_LIMIT:
        raise CreativeDailyLimitReached()

    with transaction.atomic():
        creative = queries.create_creative(user=user, look=look, format_choice=format_choice, notes=notes.strip())
        for position, upload in enumerate(photos):
            queries.add_source(creative, position, imaging.source_jpeg(upload.read()))
        transaction.on_commit(lambda: generate_creative.delay(creative.pk))
    return creative


def generate_creative(creative_id: int) -> str:
    """Celery entry point: plan the post with the copy engine, then queue one render per slide."""
    from apps.creatives.tasks import render_slide

    creative = queries.get_creative(creative_id)
    if creative is None:
        return "missing"
    if creative.status != Creative.Status.PLANNING:
        return creative.status

    try:
        brief = get_copy_engine().plan(
            _photos(creative),
            prompts.plan_request(look=creative.look, format_choice=creative.format_choice, notes=creative.notes),
        )
    except HookdError as exc:
        queries.update_creative(creative, status=Creative.Status.FAILED, error=exc.message)
        return creative.status

    shots = [(shot.role, shot.prompt) for shot in brief.slides]
    if creative.format_choice == FormatChoice.SINGLE:
        shots = shots[:1]
    slides = queries.save_plan(
        creative,
        brief={"product": brief.product, "branding": brief.branding, "audience": brief.audience},
        caption=brief.caption.strip(),
        hashtags=format_hashtags(brief.hashtags),
        questions=[
            {"label": q.label, "question": q.question, "scope": q.scope, "answer": q.answer} for q in brief.questions
        ],
        shots=shots,
    )
    for slide in slides:
        transaction.on_commit(lambda slide_id=slide.pk: render_slide.delay(slide_id))
    return creative.status


def render_slide(slide_id: int) -> str:
    """Celery entry point: render one slide, then settle the draft's status."""
    slide = queries.get_slide(slide_id)
    if slide is None:
        return "missing"
    if slide.status != CreativeSlide.Status.PENDING:
        return slide.status

    creative = slide.creative
    prompt = prompts.image_prompt(prompt=slide.prompt, branding=creative.brief.get("branding") or [], tweak=slide.tweak)
    try:
        image = imaging.post_jpeg(get_image_engine().render(_photos(creative), prompt))
    except HookdError as exc:
        queries.mark_slide_failed(slide, exc.message)
    else:
        queries.save_slide_image(slide, image)
    _settle(creative)
    return slide.status


def _settle(creative: Creative) -> None:
    """Ready once no slide is still rendering and at least one worked; failed when none did."""
    statuses = queries.slide_statuses(creative)
    if creative.status == Creative.Status.PUBLISHED or CreativeSlide.Status.PENDING in statuses:
        return
    if CreativeSlide.Status.READY in statuses:
        queries.update_creative(creative, status=Creative.Status.READY, error="")
    else:
        queries.update_creative(creative, status=Creative.Status.FAILED, error="None of the photos could be made. Please try again.")


def _photos(creative: Creative) -> list[ProductPhoto]:
    photos = []
    for source in queries.list_sources(creative):
        with source.file.open("rb") as handle:
            photos.append(ProductPhoto(data=handle.read()))
    return photos


# Editing


def regenerate_slide(*, user, slide_id: int, tweak: str = "") -> CreativeSlide:
    """Redo one photo (optionally with a change request). Only this slide is sent to the image model."""
    from apps.creatives.tasks import render_slide

    slide = queries.get_slide_for_user(user, slide_id)
    if slide is None:
        raise SlideNotFound()
    creative = slide.creative
    if creative.status not in (Creative.Status.READY, Creative.Status.FAILED) or not creative.brief:
        raise CreativeNotReady()
    with transaction.atomic():
        queries.mark_slide_pending(slide, tweak.strip())
        queries.update_creative(creative, status=Creative.Status.GENERATING, error="")
        transaction.on_commit(lambda: render_slide.delay(slide.pk))
    return slide


def update_copy(*, user, creative_id: int, caption: str, hashtags: str, answers: dict[int, str] | None = None) -> Creative:
    """Save the edited caption and hashtags, and the seller's answers to the reply agent questions (by index)."""
    creative = _editable(user, creative_id)
    caption = caption.strip()
    hashtags = format_hashtags(HASHTAG_SEPARATORS.split(hashtags))
    if len(full_caption(caption, hashtags)) > CAPTION_MAX_LENGTH:
        raise CreativeCaptionTooLong()
    questions = [
        {**item, "answer": (answers or {}).get(index, item.get("answer", "")).strip()}
        for index, item in enumerate(creative.brain_questions)
    ]
    return queries.update_creative(creative, caption=caption, hashtags=hashtags, brain_questions=questions)


def rewrite_caption(*, user, creative_id: int, instruction: str = "") -> Creative:
    """One short text call from the stored brief. No photos are sent."""
    creative = _editable(user, creative_id)
    copy = get_copy_engine().rewrite(
        prompts.rewrite_request(
            brief=creative.brief, caption=creative.caption, hashtags=creative.hashtags, instruction=instruction
        )
    )
    return queries.update_creative(creative, caption=copy.caption.strip(), hashtags=format_hashtags(copy.hashtags))


def delete_creative(*, user, creative_id: int) -> None:
    queries.delete_creative(get_user_creative(user=user, creative_id=creative_id))


def _editable(user, creative_id: int) -> Creative:
    creative = get_user_creative(user=user, creative_id=creative_id)
    if creative.status not in (Creative.Status.READY, Creative.Status.GENERATING) or not creative.brief:
        raise CreativeNotReady()
    return creative


# Publishing


def publish_creative(*, user, creative_id: int, account_id: int):
    """Hand the ready photos to the normal publish pipeline. Returns the PublishJob."""
    creative = get_user_creative(user=user, creative_id=creative_id)
    if creative.status != Creative.Status.READY:
        raise CreativeNotReady()
    account = account_queries.get_account_for_user(user, account_id)
    if account is None or not content_services.can_publish(account):
        raise HookdError("Choose an account that can publish.")
    slides = queries.ready_slides(creative)
    if not slides:
        raise CreativeNotReady()
    caption = full_caption(creative.caption, creative.hashtags)
    if len(caption) > CAPTION_MAX_LENGTH:
        raise CreativeCaptionTooLong()

    uploads = []
    for slide in slides:
        with slide.image.open("rb") as handle:
            uploads.append(SimpleUploadedFile(f"slide-{slide.position}.jpg", handle.read(), content_type="image/jpeg"))
    kind = PublishKind.CAROUSEL if len(uploads) > 1 else PublishKind.IMAGE
    job = content_services.create_publish_job(account=account, kind=kind, caption=caption, uploads=uploads)
    status = creative.status if job.status == job.Status.FAILED else Creative.Status.PUBLISHED
    queries.update_creative(creative, publish_job=job, status=status)
    if job.post is not None:
        # Images usually go live at once, before the draft is linked to the job, so the signal found no draft.
        save_brain_answers(creative, job.post)
    return job


# Reply agent brains


def save_brain_answers_for_job(job, post) -> None:
    """post_published receiver: a draft that went live later (still processing when posted) teaches the agent now."""
    creative = queries.find_creative_for_job(job)
    if creative is not None:
        save_brain_answers(creative, post)


def save_brain_answers(creative: Creative, post) -> None:
    """Answered questions become brain lines ("Label: answer"): product facts in the post's video brain, shop facts
    in the account brain. A line with the same label is replaced. Runs once per draft."""
    if creative.brain_saved_at is not None:
        return
    lines = {"post": [], "account": []}
    for item in creative.answered_questions:
        lines[item.get("scope", "post") if item.get("scope") in lines else "post"].append(
            f"{item['label'].strip()}: {item['answer'].strip()}"
        )
    if lines["post"]:
        brain = agent_services.get_post_brain(post)
        agent_services.save_post_brain(
            post,
            knowledge=merge_brain_lines(brain.knowledge if brain else "", lines["post"]),
            agent_enabled=brain.agent_enabled if brain else True,
        )
    if lines["account"]:
        profile = agent_services.get_profile(post.account)
        agent_services.update_profile(post.account, knowledge=merge_brain_lines(profile.knowledge, lines["account"]))
    queries.update_creative(creative, brain_saved_at=timezone.now())


def merge_brain_lines(existing: str, new_lines: list[str]) -> str:
    labels = {line.split(":", 1)[0].strip().lower() for line in new_lines}
    kept = [
        line for line in existing.splitlines()
        if line.strip() and line.split(":", 1)[0].strip().lower() not in labels
    ]
    return "\n".join([*kept, *new_lines])


# Files


def user_file(*, user, kind: str, file_id: int):
    """A slide image or source photo of the user's own draft, for the preview."""
    item = queries.get_slide_for_user(user, file_id) if kind == "slide" else queries.get_source_for_user(user, file_id)
    field = getattr(item, "image" if kind == "slide" else "file", None) if item else None
    if not field:
        raise SlideNotFound()
    return field


# Formatting


def format_hashtags(tags: list[str]) -> str:
    """'#tag' words separated by spaces: no duplicates, no punctuation, at most 30 (Instagram's limit)."""
    seen = []
    for tag in tags:
        clean = HASHTAG_PATTERN.sub("", tag.lstrip("#")).lower()
        if clean and clean not in seen:
            seen.append(clean)
    return " ".join(f"#{tag}" for tag in seen[:MAX_HASHTAGS])


def full_caption(caption: str, hashtags: str) -> str:
    return f"{caption}\n\n{hashtags}".strip() if hashtags else caption.strip()

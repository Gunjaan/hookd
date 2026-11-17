"""All database access for the post studio lives here."""

from datetime import datetime
from decimal import Decimal

from django.core.files.base import ContentFile
from django.db.models import F, Prefetch, QuerySet, Sum
from django.db.models.functions import Coalesce

from apps.creatives.models import Creative, CreativeAiCall, CreativeSlide, CreativeSlideVersion, CreativeSource


def create_creative(*, user, look: str, format_choice: str, notes: str) -> Creative:
    return Creative.objects.create(user=user, look=look, format_choice=format_choice, notes=notes)


def add_source(creative: Creative, position: int, data: bytes) -> CreativeSource:
    source = CreativeSource(creative=creative, position=position)
    source.file.save("source.jpg", ContentFile(data), save=False)
    source.save()
    return source


def list_sources(creative: Creative) -> list[CreativeSource]:
    return list(creative.sources.all())


def count_creatives_since(user, since: datetime) -> int:
    return Creative.objects.filter(user=user, created_at__gte=since).count()


def get_creative(creative_id: int) -> Creative | None:
    return Creative.objects.filter(pk=creative_id).first()


def get_creative_for_user(user, creative_id: int) -> Creative | None:
    return Creative.objects.filter(pk=creative_id, user=user).prefetch_related("slides__versions", "sources").first()


def list_creatives_for_user(user, limit: int = 30) -> QuerySet[Creative]:
    return Creative.objects.filter(user=user).prefetch_related(
        Prefetch("slides", queryset=CreativeSlide.objects.order_by("position"))
    )[:limit]


def update_creative(creative: Creative, **fields) -> Creative:
    for name, value in fields.items():
        setattr(creative, name, value)
    creative.save(update_fields=[*fields, "updated_at"])
    return creative


def save_plan(
    creative: Creative, *, brief: dict, caption: str, hashtags: str, questions: list[dict], shots: list[tuple[str, str]]
) -> list[CreativeSlide]:
    update_creative(
        creative, brief=brief, caption=caption, hashtags=hashtags, brain_questions=questions,
        status=Creative.Status.GENERATING, error="",
    )
    return [
        CreativeSlide.objects.create(creative=creative, position=index, role=role[:64], prompt=prompt)
        for index, (role, prompt) in enumerate(shots)
    ]


def find_creative_for_job(job) -> Creative | None:
    return Creative.objects.filter(publish_job=job).first()


def get_slide(slide_id: int) -> CreativeSlide | None:
    return CreativeSlide.objects.select_related("creative").filter(pk=slide_id).first()


def get_slide_for_user(user, slide_id: int) -> CreativeSlide | None:
    return CreativeSlide.objects.select_related("creative").filter(pk=slide_id, creative__user=user).first()


def get_source_for_user(user, source_id: int) -> CreativeSource | None:
    return CreativeSource.objects.filter(pk=source_id, creative__user=user).first()


def _archive_image(slide: CreativeSlide) -> None:
    """Keep the slide's current image as an earlier version. The version takes over the file, nothing is copied."""
    if slide.image:
        CreativeSlideVersion.objects.create(slide=slide, image=slide.image.name, tweak=slide.tweak)


def mark_slide_pending(slide: CreativeSlide, tweak: str) -> CreativeSlide:
    _archive_image(slide)
    slide.image = ""
    slide.status = CreativeSlide.Status.PENDING
    slide.tweak = tweak
    slide.error = ""
    slide.save(update_fields=["image", "status", "tweak", "error", "updated_at"])
    return slide


def save_slide_image(slide: CreativeSlide, data: bytes) -> CreativeSlide:
    slide.image.save("slide.jpg", ContentFile(data), save=False)
    slide.status = CreativeSlide.Status.READY
    slide.error = ""
    slide.save(update_fields=["image", "status", "error", "updated_at"])
    return slide


def get_version_for_user(user, version_id: int) -> CreativeSlideVersion | None:
    return (
        CreativeSlideVersion.objects.select_related("slide__creative")
        .filter(pk=version_id, slide__creative__user=user)
        .first()
    )


def swap_version_into_slide(version: CreativeSlideVersion) -> CreativeSlide:
    """Put an earlier image back on its slide. The image it replaces becomes a version itself."""
    slide = version.slide
    _archive_image(slide)
    slide.image = version.image.name
    slide.tweak = version.tweak
    slide.status = CreativeSlide.Status.READY
    slide.error = ""
    slide.save(update_fields=["image", "tweak", "status", "error", "updated_at"])
    version.delete()
    return slide


def mark_slide_failed(slide: CreativeSlide, error: str) -> CreativeSlide:
    slide.status = CreativeSlide.Status.FAILED
    slide.error = error
    slide.save(update_fields=["status", "error", "updated_at"])
    return slide


def slide_statuses(creative: Creative) -> list[str]:
    return list(CreativeSlide.objects.filter(creative=creative).values_list("status", flat=True))


def ready_slides(creative: Creative) -> list[CreativeSlide]:
    return list(CreativeSlide.objects.filter(creative=creative, status=CreativeSlide.Status.READY).order_by("position"))


def add_ai_call(
    creative: Creative, *, kind: str, model: str, input_tokens: int, output_tokens: int, cost_usd: Decimal,
    slide: CreativeSlide | None = None,
) -> CreativeAiCall:
    return CreativeAiCall.objects.create(
        creative=creative, slide=slide, kind=kind, model=model,
        input_tokens=input_tokens, output_tokens=output_tokens, cost_usd=cost_usd,
    )


def with_usage_totals(queryset: QuerySet[Creative]) -> QuerySet[Creative]:
    """Annotate drafts with `total_tokens` and `total_cost` over their AI calls (admin list)."""
    return queryset.annotate(
        total_tokens=Coalesce(Sum(F("ai_calls__input_tokens") + F("ai_calls__output_tokens")), 0),
        total_cost=Coalesce(Sum("ai_calls__cost_usd"), Decimal("0")),
    )


def usage_totals(queryset: QuerySet[CreativeAiCall]) -> dict:
    """Calls, tokens and cost over a set of AI calls (admin changelist totals)."""
    return queryset.aggregate(
        input_tokens=Coalesce(Sum("input_tokens"), 0),
        output_tokens=Coalesce(Sum("output_tokens"), 0),
        cost=Coalesce(Sum("cost_usd"), Decimal("0")),
    )


def delete_creative(creative: Creative) -> None:
    files = [source.file for source in creative.sources.all()]
    for slide in creative.slides.all():
        files += [slide.image] if slide.image else []
        files += [version.image for version in slide.versions.all()]
    creative.delete()
    for item in files:
        item.storage.delete(item.name)

"""All database access for the post studio lives here."""

from datetime import datetime

from django.core.files.base import ContentFile
from django.db.models import Prefetch, QuerySet

from apps.creatives.models import Creative, CreativeSlide, CreativeSource


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
    return Creative.objects.filter(pk=creative_id, user=user).prefetch_related("slides", "sources").first()


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


def mark_slide_pending(slide: CreativeSlide, tweak: str) -> CreativeSlide:
    slide.status = CreativeSlide.Status.PENDING
    slide.tweak = tweak
    slide.error = ""
    slide.save(update_fields=["status", "tweak", "error", "updated_at"])
    return slide


def save_slide_image(slide: CreativeSlide, data: bytes) -> CreativeSlide:
    old = slide.image.name
    slide.image.save("slide.jpg", ContentFile(data), save=False)
    slide.status = CreativeSlide.Status.READY
    slide.error = ""
    slide.save(update_fields=["image", "status", "error", "updated_at"])
    if old:
        slide.image.storage.delete(old)
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


def delete_creative(creative: Creative) -> None:
    files = [source.file for source in creative.sources.all()] + [slide.image for slide in creative.slides.all() if slide.image]
    creative.delete()
    for item in files:
        item.storage.delete(item.name)

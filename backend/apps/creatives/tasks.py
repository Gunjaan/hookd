"""Celery tasks for the post studio. Each task is a thin wrapper around a service."""

from celery import shared_task

from apps.creatives import services


@shared_task
def generate_creative(creative_id: int) -> str:
    """Plan the post (one text call) and queue the slide renders. Returns the draft status."""
    return services.generate_creative(creative_id)


@shared_task
def render_slide(slide_id: int) -> str:
    """Render one photo of a draft (one image call). Returns the slide status."""
    return services.render_slide(slide_id)

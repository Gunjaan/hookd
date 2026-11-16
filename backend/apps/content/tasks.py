"""Celery tasks for the content app. Each task is a thin wrapper around a service."""

from celery import shared_task

from apps.content import services


@shared_task
def poll_publish_job(job_id: int) -> str:
    """Check a processing upload on the platform; reschedules itself until it is published or fails.

    Returns the job status, which is stored as the task result.
    """
    return services.poll_publish_job(job_id)

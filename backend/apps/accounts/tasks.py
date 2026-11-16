"""Celery tasks for accounts. Each task is a thin wrapper around a service."""

from smtplib import SMTPException

from celery import shared_task

from apps.accounts import services


@shared_task(autoretry_for=(SMTPException, OSError), retry_backoff=60, max_retries=5)
def send_access_request(details: dict) -> str:
    """Email an access request to support. Retries with backoff while the mail server is unreachable."""
    services.send_access_request_email(details)
    return "sent"

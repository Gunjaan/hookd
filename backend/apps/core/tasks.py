"""Celery tasks for core. Each task is a thin wrapper around a service."""

from smtplib import SMTPException

from celery import shared_task

from apps.core import services


@shared_task(autoretry_for=(SMTPException, OSError), retry_backoff=60, max_retries=5)
def send_contact_message(details: dict) -> str:
    """Email a contact form message to support. Retries with backoff while the mail server is unreachable."""
    services.send_contact_email(details)
    return "sent"

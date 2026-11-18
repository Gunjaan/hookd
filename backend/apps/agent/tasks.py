"""Celery tasks for the reply agent. Each task is a thin wrapper around a service and returns its outcome, stored as
the task result."""

from smtplib import SMTPException

from celery import shared_task

from apps.agent import services


@shared_task
def handle_comment(comment_id: int) -> str:
    """Start an agent run for a new comment."""
    return services.handle_new_comment(comment_id)


@shared_task
def handle_buyer_message(conversation_id: int, message_id: int) -> str:
    """Start or continue the buyer's agent run for a new DM."""
    return services.handle_buyer_message(conversation_id, message_id)


@shared_task
def advance_run(run_id: int) -> str:
    """Let the agent handle what is new on a run (also fired at a follow-up's time)."""
    return services.advance_run(run_id)


@shared_task
def send_reply(reply_id: int) -> str:
    """Send one queued public reply within the sending limits."""
    return services.send_reply(reply_id)


@shared_task
def send_dm(dm_id: int) -> str:
    """Send one queued DM within the sending limits and Meta's messaging rules."""
    return services.send_dm(dm_id)


@shared_task(autoretry_for=(SMTPException, OSError), retry_backoff=60, max_retries=5)
def email_ticket(ticket_id: int) -> str:
    """Email a support ticket to the support address. Retries with backoff while the mail server is unreachable."""
    return services.email_ticket(ticket_id)


@shared_task
def learn_from_post(post_id: int) -> str:
    """Learn the facts in a post's caption."""
    return services.learn_from_post(post_id)


@shared_task
def learn_from_reply(reply_id: int) -> str:
    """Learn the facts in a reply the creator wrote."""
    return services.learn_from_reply(reply_id)

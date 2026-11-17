"""Celery tasks for the reply agent. Each task is a thin wrapper around a service."""

from celery import shared_task

from apps.agent import services


@shared_task
def handle_comment(comment_id: int) -> str:
    """Decide what to do with a new comment. Returns the outcome, stored as the task result."""
    return services.handle_new_comment(comment_id)


@shared_task
def send_reply(reply_id: int) -> str:
    """Send one queued reply within the sending limits. Returns the outcome, stored as the task result."""
    return services.send_reply(reply_id)


@shared_task
def learn_from_post(post_id: int) -> str:
    """Learn the facts in a post's caption. Returns the outcome, stored as the task result."""
    return services.learn_from_post(post_id)


@shared_task
def learn_from_reply(reply_id: int) -> str:
    """Learn the facts in a reply the creator wrote. Returns the outcome, stored as the task result."""
    return services.learn_from_reply(reply_id)

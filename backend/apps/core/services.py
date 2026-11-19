"""Core services: the landing page contact form."""

from django.conf import settings
from django.core.cache import cache
from django.core.mail import EmailMessage

from apps.core.exceptions import HookdError

CONTACT_LIMIT_PER_HOUR = 5


class ContactLimitReached(HookdError):
    default_message = "You have sent a few messages already. Please try again in an hour, or email support@hookd.world."


def client_ip(request) -> str:
    """The visitor's address. Caddy replaces X-Forwarded-For with the real client, so its last entry is trusted."""
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[-1].strip()
    return request.META.get("REMOTE_ADDR", "")


def send_contact(details: dict, ip: str) -> None:
    """Queue one contact message for the support inbox, at most CONTACT_LIMIT_PER_HOUR an hour per address."""
    from apps.core.tasks import send_contact_message

    key = f"contact:{ip}"
    cache.add(key, 0, timeout=60 * 60)
    if cache.incr(key) > CONTACT_LIMIT_PER_HOUR:
        raise ContactLimitReached()
    send_contact_message.delay(details)


def send_contact_email(details: dict) -> None:
    """Email one contact message to the support inbox. Replying goes straight to the sender."""
    EmailMessage(
        subject=f"Hookd contact: {details['name']}",
        body=f"New message from the hookd.world contact form\n\nName: {details['name']}\nEmail: {details['email']}\n\n"
        f"{details['message']}\n",
        to=[settings.SUPPORT_EMAIL],
        reply_to=[details["email"]],
    ).send()

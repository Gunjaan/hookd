"""Account services: access requests while self serve signup is closed."""

from django.conf import settings
from django.core.mail import EmailMessage

ACCESS_REQUEST_FIELDS = (
    ("name", "Name"),
    ("email", "Email"),
    ("instagram", "Instagram"),
    ("phone", "Phone or WhatsApp"),
    ("about", "Creates or sells"),
)


def request_access(details: dict) -> None:
    """Queue the access request email. `details` is AccessRequestForm.cleaned_data."""
    from apps.accounts.tasks import send_access_request

    send_access_request.delay(details)


def send_access_request_email(details: dict) -> None:
    """Email one access request to the support inbox. Replying goes straight to the applicant."""
    lines = [f"{label}: {details.get(key) or '-'}" for key, label in ACCESS_REQUEST_FIELDS]
    EmailMessage(
        subject=f"Hookd access request: {details['name']} ({details['instagram']})",
        body="New access request from hookd.world\n\n" + "\n".join(lines) + "\n",
        to=[settings.SUPPORT_EMAIL],
        reply_to=[details["email"]],
    ).send()

from django import template

from apps.integrations.constants import Capability
from apps.integrations.models import SocialAccount

register = template.Library()

CAPABILITY_LABELS = {
    Capability.PUBLIC_REPLY: "Comment replies",
    Capability.PRIVATE_REPLY: "Private replies",
    Capability.DIRECT_MESSAGE: "DMs",
    Capability.HIDE_COMMENT: "Hide comments",
    Capability.PUBLISH: "Publishing",
    Capability.REALTIME_WEBHOOKS: "Realtime",
    Capability.INSIGHTS: "Insights",
}

STATUS_TONES = {
    SocialAccount.Status.ACTIVE: "success",
    SocialAccount.Status.EXPIRED: "warning",
    SocialAccount.Status.REVOKED: "danger",
    SocialAccount.Status.ERROR: "danger",
}


@register.filter
def capability_label(capability) -> str:
    return CAPABILITY_LABELS.get(capability, str(capability))


@register.filter
def status_tone(status) -> str:
    return STATUS_TONES.get(status, "neutral")

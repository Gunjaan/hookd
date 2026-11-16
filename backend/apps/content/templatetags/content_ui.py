from django import template
from django.contrib.humanize.templatetags.humanize import intcomma

from apps.content.models import PublishJob

register = template.Library()

JOB_TONES = {
    PublishJob.Status.PROCESSING: "info",
    PublishJob.Status.PUBLISHED: "success",
    PublishJob.Status.FAILED: "danger",
}


@register.filter
def metric_display(metric) -> str:
    """Format a metric dict or Metric: counts with separators, durations as 1h 2m, 3m 4s or 5.6s."""
    unit = metric["unit"] if isinstance(metric, dict) else metric.unit
    value = metric["value"] if isinstance(metric, dict) else metric.value
    if unit != "milliseconds":
        return intcomma(value)
    seconds = value / 1000
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, seconds = divmod(int(seconds), 60)
    if minutes < 60:
        return f"{minutes}m {seconds}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"


@register.filter
def job_tone(status) -> str:
    return JOB_TONES.get(status, "neutral")

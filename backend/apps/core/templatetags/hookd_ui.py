import re

from django import template

register = template.Library()


@register.filter
def initials(name) -> str:
    """Up to two uppercase initials for an avatar fallback, for example 'asha.edits' -> 'AE'."""
    parts = [part for part in re.split(r"[\s._]+", str(name or "").lstrip("@")) if part]
    return "".join(part[0].upper() for part in parts[:2])

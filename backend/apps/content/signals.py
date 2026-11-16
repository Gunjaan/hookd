"""Signals the content app sends so other apps can react without content importing them."""

from django.dispatch import Signal

# Sent once a publish job is live. Keyword arguments: job (PublishJob), post (Post).
post_published = Signal()

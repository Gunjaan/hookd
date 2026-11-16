"""Loads the reply engine named by settings.HOOKD_REPLY_ENGINE (a dotted path)."""

from django.conf import settings
from django.utils.module_loading import import_string

from apps.agent.engines.base import ReplyEngine


def get_engine() -> ReplyEngine:
    return import_string(settings.HOOKD_REPLY_ENGINE)()

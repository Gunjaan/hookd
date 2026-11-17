"""Loads the reply engine and brain learner named by settings.HOOKD_REPLY_ENGINE and HOOKD_BRAIN_LEARNER (dotted paths)."""

from django.conf import settings
from django.utils.module_loading import import_string

from apps.agent.engines.base import BrainLearner, ReplyEngine


def get_engine() -> ReplyEngine:
    return import_string(settings.HOOKD_REPLY_ENGINE)()


def get_learner() -> BrainLearner:
    return import_string(settings.HOOKD_BRAIN_LEARNER)()

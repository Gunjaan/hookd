"""Loads the agent model and brain learner named by settings.HOOKD_AGENT_MODEL and HOOKD_BRAIN_LEARNER (dotted paths)."""

from django.conf import settings
from django.utils.module_loading import import_string

from apps.agent.engines.base import AgentModel, BrainLearner


def get_model() -> AgentModel:
    return import_string(settings.HOOKD_AGENT_MODEL)()


def get_learner() -> BrainLearner:
    return import_string(settings.HOOKD_BRAIN_LEARNER)()

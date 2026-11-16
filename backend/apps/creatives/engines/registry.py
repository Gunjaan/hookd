"""Loads the engines named by settings.HOOKD_COPY_ENGINE and settings.HOOKD_IMAGE_ENGINE (dotted paths)."""

from django.conf import settings
from django.utils.module_loading import import_string

from apps.creatives.engines.base import CopyEngine, ImageEngine


def get_copy_engine() -> CopyEngine:
    return import_string(settings.HOOKD_COPY_ENGINE)()


def get_image_engine() -> ImageEngine:
    return import_string(settings.HOOKD_IMAGE_ENGINE)()

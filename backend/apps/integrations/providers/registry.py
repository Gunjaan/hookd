"""Looks up provider instances from settings.HOOKD_PROVIDERS."""

from functools import lru_cache

from django.conf import settings
from django.utils.module_loading import import_string

from apps.integrations.exceptions import ProviderNotFound
from apps.integrations.providers.base import BaseProvider


@lru_cache(maxsize=1)
def _provider_classes() -> dict[str, type[BaseProvider]]:
    classes: dict[str, type[BaseProvider]] = {}
    for path in settings.HOOKD_PROVIDERS:
        cls = import_string(path)
        classes[cls.platform] = cls
    return classes


def get_provider(platform: str) -> BaseProvider:
    try:
        return _provider_classes()[platform]()
    except KeyError as exc:
        raise ProviderNotFound() from exc


def all_providers() -> list[BaseProvider]:
    return [cls() for cls in _provider_classes().values()]

"""Symmetric encryption for secrets stored in the database (OAuth tokens)."""

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


class DecryptionError(Exception):
    pass


@lru_cache(maxsize=1)
def _cipher() -> MultiFernet:
    keys = settings.FIELD_ENCRYPTION_KEYS
    if not keys:
        raise ImproperlyConfigured(
            "FIELD_ENCRYPTION_KEYS is empty. Generate a key with "
            "`python manage.py generate_encryption_key`."
        )
    return MultiFernet([Fernet(key.encode()) for key in keys])


def encrypt(value: str) -> str:
    return _cipher().encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    try:
        return _cipher().decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise DecryptionError("Stored value could not be decrypted with the configured keys.") from exc


def generate_key() -> str:
    return Fernet.generate_key().decode()

from django.db import models

from apps.core.crypto import decrypt, encrypt


class EncryptedTextField(models.TextField):
    """Text column encrypted with Fernet at rest.

    Values are transparently decrypted when loaded. Encrypted columns cannot be
    used in filters because the ciphertext is non-deterministic.
    """

    def from_db_value(self, value, expression, connection):
        if value in (None, ""):
            return value
        return decrypt(value)

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if value in (None, ""):
            return value
        return encrypt(value)

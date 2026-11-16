"""All database access for the accounts app lives here."""

from apps.accounts.models import User


def email_is_registered(email: str) -> bool:
    return User.objects.filter(email__iexact=email).exists()


def create_user(*, email: str, password: str, name: str = "") -> User:
    return User.objects.create_user(email=email, password=password, name=name)


from django.core.management.base import BaseCommand

from apps.core.crypto import generate_key


class Command(BaseCommand):
    help = "Print a new Fernet key for FIELD_ENCRYPTION_KEYS."

    def handle(self, *args, **options):
        self.stdout.write(generate_key())

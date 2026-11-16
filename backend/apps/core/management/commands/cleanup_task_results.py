from django.conf import settings
from django.core.management.base import BaseCommand

from config.celery import app


class Command(BaseCommand):
    help = "Delete stored Celery task results older than CELERY_RESULT_EXPIRES. Run daily from cron."

    def handle(self, *args, **options):
        app.backend.cleanup()
        self.stdout.write(f"Removed task results older than {settings.CELERY_RESULT_EXPIRES.days} days.")

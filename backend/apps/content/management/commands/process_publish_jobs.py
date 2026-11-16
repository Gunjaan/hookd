from django.core.management.base import BaseCommand

from apps.content.services import advance_processing_jobs


class Command(BaseCommand):
    help = "Advance every processing publish job now. Manual recovery when the Celery queue was lost."

    def handle(self, *args, **options):
        summary = advance_processing_jobs()
        self.stdout.write(
            f"Published {len(summary.published)}, failed {len(summary.failed)}, still processing {len(summary.processing)}."
        )

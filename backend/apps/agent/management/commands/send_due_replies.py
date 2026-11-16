from django.core.management.base import BaseCommand

from apps.agent.services import send_overdue_replies


class Command(BaseCommand):
    help = "Send queued replies whose Celery task was lost. Safe to run any time: a reply is never sent twice."

    def handle(self, *args, **options):
        self.stdout.write(f"Sent {send_overdue_replies()} overdue replies.")

from django.core.management.base import BaseCommand

from apps.agent.services import expire_questions, send_overdue_dms, wake_overdue_runs


class Command(BaseCommand):
    help = (
        "Send queued DMs and wake follow-ups whose Celery task was lost, and expire questions the seller left open. "
        "Safe to run any time."
    )

    def handle(self, *args, **options):
        expired = expire_questions()
        self.stdout.write(f"Sent {send_overdue_dms()} overdue DMs, woke {wake_overdue_runs()} agent runs, expired {expired} questions.")

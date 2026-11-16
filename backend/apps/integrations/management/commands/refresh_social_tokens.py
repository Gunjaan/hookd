from datetime import timedelta

from django.core.management.base import BaseCommand

from apps.integrations.services import refresh_expiring_tokens


class Command(BaseCommand):
    help = "Refresh access tokens that expire within the given window. Run daily from cron."

    def add_arguments(self, parser):
        parser.add_argument("--within-days", type=int, default=10, help="Refresh tokens expiring within N days.")
        parser.add_argument("--platform", default=None, help="Limit to one platform, for example instagram.")

    def handle(self, *args, **options):
        summary = refresh_expiring_tokens(
            window=timedelta(days=options["within_days"]),
            platform=options["platform"],
        )
        self.stdout.write(f"Refreshed {len(summary.refreshed)} account(s).")
        for account_id, reason in summary.failed.items():
            self.stderr.write(f"Account {account_id} failed: {reason}")

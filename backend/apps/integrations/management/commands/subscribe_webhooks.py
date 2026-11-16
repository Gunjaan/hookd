from django.core.management.base import BaseCommand

from apps.integrations.services import subscribe_all_webhooks


class Command(BaseCommand):
    help = "Re-subscribe every active account to its platform's webhooks with the stored token. Run after changing webhook fields."

    def add_arguments(self, parser):
        parser.add_argument("--platform", default=None, help="Limit to one platform, for example instagram.")

    def handle(self, *args, **options):
        summary = subscribe_all_webhooks(platform=options["platform"])
        self.stdout.write(f"Subscribed {len(summary.subscribed)} account(s).")
        for account_id, reason in summary.failed.items():
            self.stderr.write(f"Account {account_id} failed: {reason}")

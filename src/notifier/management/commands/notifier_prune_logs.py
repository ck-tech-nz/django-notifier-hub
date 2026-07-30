"""Delete NotificationLog rows past the retention window.

``LOG_RETENTION_DAYS`` is a policy, not a mechanism: nothing deletes anything
until this command runs, so the host project must schedule it.
"""

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from notifier.conf import notifier_settings
from notifier.models import NotificationLog


class Command(BaseCommand):
    help = "Delete notification logs older than LOG_RETENTION_DAYS."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=None)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        days = (
            options["days"] if options["days"] is not None else notifier_settings.LOG_RETENTION_DAYS
        )
        if days is None:
            raise CommandError(
                "LOG_RETENTION_DAYS is None (keep forever). Pass --days N to prune anyway."
            )
        if days < 0:
            raise CommandError("--days must be zero or greater.")

        cutoff = timezone.now() - timezone.timedelta(days=days)
        queryset = NotificationLog.objects.filter(created_at__lt=cutoff)
        count = queryset.count()

        if options["dry_run"]:
            self.stdout.write(f"Would delete {count} log row(s) created before {cutoff:%Y-%m-%d}.")
            return

        queryset.delete()
        self.stdout.write(
            self.style.SUCCESS(f"Deleted {count} log row(s) created before {cutoff:%Y-%m-%d}.")
        )

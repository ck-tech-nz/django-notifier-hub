"""Re-dispatch notifications left at ``failed``, and reclaim ones stuck at ``sending``."""

import re

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from notifier.models import Channel, Notification, Status

_DURATION = re.compile(r"^(\d+)([smhd])$")
_UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}


class Command(BaseCommand):
    help = "Re-dispatch failed notifications, and optionally ones stranded at sending."

    def add_arguments(self, parser):
        parser.add_argument("--older-than", default=None, help="e.g. 30m, 2h, 1d")
        parser.add_argument("--limit", type=int, default=None)
        parser.add_argument("--channel", default=None, choices=[c.value for c in Channel])
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument(
            "--include-sending",
            action="store_true",
            help=(
                "Also pick up rows stranded at 'sending' by a worker that died mid-delivery. "
                "Requires --older-than, because a row that is legitimately in flight looks "
                "exactly the same."
            ),
        )

    def handle(self, *args, **options):
        statuses = [Status.FAILED]

        if options["include_sending"]:
            # A worker killed inside the backend's send() leaves the row at
            # 'sending' with no log row, so nothing else can find it. But a row
            # that a live worker is legitimately working on is indistinguishable,
            # and re-dispatching that one would double-send -- hence the
            # mandatory age threshold.
            if not options["older_than"]:
                raise CommandError(
                    "--include-sending requires --older-than, so that rows a worker is "
                    "still legitimately delivering are not re-sent. Use a threshold "
                    "comfortably longer than your slowest send, e.g. --older-than 30m."
                )
            statuses.append(Status.SENDING)

        queryset = Notification.objects.filter(status__in=statuses)

        if options["channel"]:
            queryset = queryset.filter(channel=options["channel"])

        if options["older_than"]:
            match = _DURATION.match(options["older_than"])
            if not match:
                raise CommandError("--older-than must look like 30m, 2h or 1d.")
            amount, unit = int(match.group(1)), _UNITS[match.group(2)]
            queryset = queryset.filter(
                updated_at__lt=timezone.now() - timezone.timedelta(**{unit: amount})
            )

        queryset = queryset.order_by("created_at")
        if options["limit"]:
            queryset = queryset[: options["limit"]]

        notifications = list(queryset)
        if options["dry_run"]:
            self.stdout.write(f"Would re-dispatch {len(notifications)} notification(s).")
            for notification in notifications:
                self.stdout.write(
                    f"  #{notification.pk} {notification.channel} ({notification.status})"
                )
            return

        for notification in notifications:
            notification.resend()

        self.stdout.write(
            self.style.SUCCESS(f"Re-dispatched {len(notifications)} notification(s).")
        )

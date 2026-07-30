"""Smoke-test the configuration end to end, without touching business data."""

from django.core.management.base import BaseCommand, CommandError

from notifier.conf import notifier_settings
from notifier.models import Channel, DefaultRecipient, Notification, NotificationLog, Status


class Command(BaseCommand):
    help = "Send one throwaway notification to verify the configuration."

    def add_arguments(self, parser):
        parser.add_argument("--channel", default=Channel.EMAIL, choices=[c.value for c in Channel])
        parser.add_argument("--to", action="append", required=True, help="Repeatable.")
        parser.add_argument("--source", default="manage.py:notifier_send_test")
        parser.add_argument(
            "--keep", action="store_true", help="Do not delete the notification afterwards."
        )

    def handle(self, *args, **options):
        channel = options["channel"]
        recipients = options["to"]

        env = notifier_settings.env
        self.stdout.write(f"Environment: {env} (production={notifier_settings.is_production})")
        self.stdout.write(f"Backend:     {notifier_settings.backend_path(channel)}")
        if channel == Channel.EMAIL:
            self.stdout.write(f"Recipients:  {notifier_settings.recipient_mode} mode")

        if not notifier_settings.is_production:
            defaults = list(
                DefaultRecipient.objects.filter(channel=channel, enabled=True).values_list(
                    "address", flat=True
                )
            )
            if defaults:
                self.stdout.write(f"Redirecting to default recipients: {', '.join(defaults)}")
            elif notifier_settings.SUPPRESS_WHEN_NO_DEFAULT_RECIPIENT:
                self.stdout.write(
                    self.style.WARNING(
                        "No enabled DefaultRecipient for this channel: the send will be "
                        "suppressed and nothing will leave the process."
                    )
                )

        notification = Notification(
            channel=channel,
            status=Status.DRAFT,
            recipients=recipients,
            source=options["source"],
            subject="django-notifier-hub test message",
            body_text="If you are reading this, the notifier configuration works.",
        )
        notification.full_clean(exclude=["read_by"])
        notification.save()
        notification.mark_ready()

        notification.refresh_from_db()
        log = notification.logs.first()
        if log is None:
            raise CommandError(
                "No log row was written. If NOTIFIER['USE_CELERY'] is on, check that a worker "
                "is running -- an enqueued task that nobody consumes leaves the row at 'ready'."
            )

        style = (
            self.style.SUCCESS if log.result == NotificationLog.LogResult.SENT else self.style.ERROR
        )
        self.stdout.write(style(f"Result: {log.result} (status={notification.status})"))
        if log.error:
            self.stdout.write(f"Detail: {log.error.splitlines()[0]}")
        if log.effective_recipients != log.requested_recipients:
            self.stdout.write(f"Sent to: {', '.join(log.effective_recipients) or '(nobody)'}")

        # Housekeeping nudge: nothing prunes automatically.
        total_logs = NotificationLog.objects.count()
        retention = notifier_settings.LOG_RETENTION_DAYS
        self.stdout.write(
            f"Log table: {total_logs} row(s); LOG_RETENTION_DAYS={retention}. "
            "Nothing prunes on a timer -- schedule notifier_prune_logs yourself."
        )

        if not options["keep"]:
            notification.delete()
            self.stdout.write("Test notification deleted (pass --keep to retain it).")

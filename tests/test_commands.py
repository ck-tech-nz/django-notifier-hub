"""The three management commands."""

import pytest
from django.core import mail
from django.core.management import CommandError, call_command
from django.utils import timezone

from notifier.models import (
    Channel,
    LogResult,
    Notification,
    NotificationLog,
    Status,
)

pytestmark = pytest.mark.django_db


# -- notifier_send_test ------------------------------------------------------


def test_send_test_reports_a_successful_send(production, capsys):
    call_command("notifier_send_test", "--channel", "email", "--to", "to@example.com")

    out = capsys.readouterr().out
    assert "Result: sent" in out
    assert len(mail.outbox) == 1
    # The throwaway notification is removed by default.
    assert Notification.objects.count() == 0


def test_send_test_keeps_the_notification_on_request(production):
    call_command("notifier_send_test", "--channel", "email", "--to", "to@example.com", "--keep")

    assert Notification.objects.count() == 1
    assert Notification.objects.get().source == "manage.py:notifier_send_test"


def test_send_test_warns_when_the_send_would_be_suppressed(settings, capsys):
    settings.DJANGO_ENV = "dev"

    call_command("notifier_send_test", "--channel", "email", "--to", "to@example.com")

    out = capsys.readouterr().out
    assert "suppressed" in out
    assert mail.outbox == []


def test_send_test_names_the_default_recipients_it_will_redirect_to(
    settings, default_email_recipient, capsys
):
    settings.DJANGO_ENV = "dev"

    call_command("notifier_send_test", "--channel", "email", "--to", "real@example.com")

    out = capsys.readouterr().out
    assert "qa@example.com" in out
    assert "Result: sent" in out


def test_send_test_nudges_about_log_retention(production, capsys):
    call_command("notifier_send_test", "--channel", "email", "--to", "to@example.com")

    out = capsys.readouterr().out
    assert "LOG_RETENTION_DAYS=90" in out
    assert "notifier_prune_logs" in out


# -- notifier_prune_logs -----------------------------------------------------


@pytest.fixture
def old_and_new_logs(make_notification):
    notification = make_notification(status=Status.DRAFT)
    old = NotificationLog.objects.create(notification=notification, result=LogResult.SENT)
    recent = NotificationLog.objects.create(notification=notification, result=LogResult.SENT)
    # auto_now_add cannot be set at creation time, so move it afterwards.
    NotificationLog.objects.filter(pk=old.pk).update(
        created_at=timezone.now() - timezone.timedelta(days=120)
    )
    return old, recent


def test_prune_logs_deletes_only_what_is_past_retention(old_and_new_logs, capsys):
    old, recent = old_and_new_logs

    call_command("notifier_prune_logs")

    assert not NotificationLog.objects.filter(pk=old.pk).exists()
    assert NotificationLog.objects.filter(pk=recent.pk).exists()
    assert "Deleted 1 log row" in capsys.readouterr().out


def test_prune_logs_dry_run_deletes_nothing(old_and_new_logs, capsys):
    call_command("notifier_prune_logs", "--dry-run")

    assert NotificationLog.objects.count() == 2
    assert "Would delete 1 log row" in capsys.readouterr().out


def test_prune_logs_honours_an_explicit_window(old_and_new_logs):
    call_command("notifier_prune_logs", "--days", "0")

    assert NotificationLog.objects.count() == 0


def test_prune_logs_refuses_when_retention_is_disabled(settings, old_and_new_logs):
    settings.NOTIFIER = {**settings.NOTIFIER, "LOG_RETENTION_DAYS": None}

    with pytest.raises(CommandError, match="keep forever"):
        call_command("notifier_prune_logs")

    assert NotificationLog.objects.count() == 2


# -- notifier_retry_failed ---------------------------------------------------


@pytest.fixture
def failed_notifications(make_notification):
    stale = make_notification(status=Status.DRAFT, subject="Stale")
    fresh = make_notification(status=Status.DRAFT, subject="Fresh")
    for notification in (stale, fresh):
        Notification.objects.filter(pk=notification.pk).update(status=Status.FAILED)
    Notification.objects.filter(pk=stale.pk).update(
        updated_at=timezone.now() - timezone.timedelta(hours=5)
    )
    return stale, fresh


def test_retry_failed_redispatches_everything_failed(failed_notifications, production, capsys):
    call_command("notifier_retry_failed")

    assert len(mail.outbox) == 2
    assert Notification.objects.filter(status=Status.SENT).count() == 2
    assert "Re-dispatched 2" in capsys.readouterr().out


def test_retry_failed_honours_older_than(failed_notifications, production):
    stale, fresh = failed_notifications

    call_command("notifier_retry_failed", "--older-than", "2h")

    stale.refresh_from_db()
    fresh.refresh_from_db()
    assert stale.status == Status.SENT
    assert fresh.status == Status.FAILED
    assert len(mail.outbox) == 1


def test_retry_failed_honours_limit(failed_notifications, production):
    call_command("notifier_retry_failed", "--limit", "1")

    assert len(mail.outbox) == 1


def test_retry_failed_can_target_one_channel(failed_notifications, production, make_notification):
    sms = make_notification(
        status=Status.DRAFT,
        channel=Channel.SMS,
        recipients=["+6421234567"],
        subject="",
        body_text="T.",
    )
    Notification.objects.filter(pk=sms.pk).update(status=Status.FAILED)

    call_command("notifier_retry_failed", "--channel", "sms")

    sms.refresh_from_db()
    assert sms.status == Status.SENT
    assert mail.outbox == []


def test_retry_failed_ignores_sending_by_default(make_notification, production):
    """A row a live worker is mid-way through must not be re-dispatched."""
    stuck = make_notification(status=Status.DRAFT)
    Notification.objects.filter(pk=stuck.pk).update(status=Status.SENDING)

    call_command("notifier_retry_failed")

    stuck.refresh_from_db()
    assert stuck.status == Status.SENDING
    assert mail.outbox == []


def test_retry_failed_can_reclaim_a_stranded_sending_row(make_notification, production):
    """A worker killed inside send() strands the row at `sending` with no log.

    Nothing else can find it: it is not `failed`, so the default query misses it.
    Demonstrated against a real worker in example/smoke_celery_stuck.py.
    """
    stuck = make_notification(status=Status.DRAFT)
    Notification.objects.filter(pk=stuck.pk).update(
        status=Status.SENDING, updated_at=timezone.now() - timezone.timedelta(hours=5)
    )

    call_command("notifier_retry_failed", "--include-sending", "--older-than", "2h")

    stuck.refresh_from_db()
    assert stuck.status == Status.SENT
    assert len(mail.outbox) == 1


def test_retry_failed_leaves_a_recently_sending_row_alone(make_notification, production):
    """The age threshold is what separates "stranded" from "in flight"."""
    inflight = make_notification(status=Status.DRAFT)
    Notification.objects.filter(pk=inflight.pk).update(status=Status.SENDING)

    call_command("notifier_retry_failed", "--include-sending", "--older-than", "2h")

    inflight.refresh_from_db()
    assert inflight.status == Status.SENDING
    assert mail.outbox == []


def test_retry_failed_requires_an_age_threshold_to_touch_sending(make_notification):
    with pytest.raises(CommandError, match="requires --older-than"):
        call_command("notifier_retry_failed", "--include-sending")


def test_retry_failed_rejects_a_bad_duration(failed_notifications):
    with pytest.raises(CommandError, match="30m, 2h or 1d"):
        call_command("notifier_retry_failed", "--older-than", "soon")


def test_retry_failed_dry_run_sends_nothing(failed_notifications, production, capsys):
    call_command("notifier_retry_failed", "--dry-run")

    assert mail.outbox == []
    assert Notification.objects.filter(status=Status.FAILED).count() == 2
    assert "Would re-dispatch 2" in capsys.readouterr().out

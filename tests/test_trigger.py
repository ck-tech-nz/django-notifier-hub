"""AC-01…AC-05: what causes a send, and what does not."""

import pytest
from django.db import transaction

from notifier.models import Channel, LogResult, Notification, NotificationLog, Status

pytestmark = pytest.mark.django_db


def test_ac_01_create_ready_sends(production):
    notification = Notification.objects.create(
        channel=Channel.EMAIL,
        status=Status.READY,
        recipients=["to@example.com"],
        subject="Hello",
        body_text="Body.",
    )

    notification.refresh_from_db()
    assert notification.status == Status.SENT
    assert notification.sent_at is not None
    assert notification.logs.count() == 1
    assert notification.logs.get().result == LogResult.SENT


def test_ac_02_create_draft_sends_nothing(make_notification, production):
    notification = make_notification(status=Status.DRAFT)

    assert notification.logs.count() == 0
    assert notification.status == Status.DRAFT


def test_ac_03_draft_to_ready_sends_once_and_resave_does_not(make_notification, production):
    notification = make_notification(status=Status.DRAFT)

    notification.mark_ready()
    assert notification.logs.count() == 1

    # Already ready: re-saving must not send again.
    notification.refresh_from_db()
    notification.status = Status.READY
    notification.save()
    assert notification.logs.count() == 1

    reloaded = Notification.objects.get(pk=notification.pk)
    reloaded.save()
    assert reloaded.logs.count() == 1


def test_ac_04_archived_to_ready_sends(make_notification, production):
    notification = make_notification(status=Status.ARCHIVED)
    assert notification.logs.count() == 0

    notification.refresh_from_db()
    notification.mark_ready()

    assert notification.logs.count() == 1


def test_ac_05_dispatch_waits_for_commit(
    make_notification, production, settings, django_capture_on_commit_callbacks
):
    settings.NOTIFIER = {**settings.NOTIFIER, "DISPATCH_ON_COMMIT": True}
    notification = make_notification(status=Status.DRAFT)

    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        notification.mark_ready()
        # Still inside the block: the callback is queued, not run.
        assert notification.logs.count() == 0
    assert len(callbacks) == 1

    for callback in callbacks:
        callback()
    assert notification.logs.count() == 1


def test_ac_05_rollback_sends_nothing(production, settings):
    from django.core import mail

    settings.NOTIFIER = {**settings.NOTIFIER, "DISPATCH_ON_COMMIT": True}

    with pytest.raises(RuntimeError), transaction.atomic():
        Notification.objects.create(
            channel=Channel.EMAIL,
            status=Status.READY,
            recipients=["to@example.com"],
            subject="Hello",
            body_text="Body.",
        )
        raise RuntimeError("rolling back")

    assert Notification.objects.count() == 0
    assert NotificationLog.objects.count() == 0
    assert mail.outbox == []

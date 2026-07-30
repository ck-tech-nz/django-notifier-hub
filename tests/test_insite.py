"""AC-19, AC-20: the in-site channel and read state.

In-site is the secondary channel. `read_by` is a convenience the host project
owns, deliberately left unindexed and unoptimised (PRD 6.4.1).
"""

import pytest
from django.core import mail

from notifier.backends import sms
from notifier.models import Channel, LogResult, Notification, Status

pytestmark = pytest.mark.django_db


def make_insite(**overrides):
    fields = {
        "channel": Channel.INSITE,
        "status": Status.READY,
        "recipients": ["42", "43"],
        "subject": "Maintenance on Sunday",
        "body_html": "<p>02:00 to 04:00 UTC.</p>",
    }
    fields.update(overrides)
    return Notification.objects.create(**fields)


def test_ac_20_insite_sends_nothing_over_the_wire_but_still_logs(production):
    notification = make_insite()

    assert mail.outbox == []
    assert sms.outbox == []

    log = notification.logs.get()
    assert log.result == LogResult.SENT
    assert log.provider_response["transport"] == "none"
    assert log.provider_response["recipients"] == 2

    notification.refresh_from_db()
    assert notification.status == Status.SENT
    assert notification.sent_at is not None


def test_the_rendered_snapshot_is_what_the_host_project_reads(production):
    notification = make_insite(
        subject="Order {{ order }}",
        body_html="<p>{{ order }} is ready.</p>",
        context={"order": "A-3"},
    )

    notification.refresh_from_db()
    assert notification.rendered_subject == "Order A-3"
    assert "A-3 is ready." in notification.rendered_html
    # A plaintext alternative is derived so the record stays readable.
    assert notification.rendered_text == "A-3 is ready."


def test_ac_19_mark_read_is_idempotent(production):
    notification = make_insite()

    notification.mark_read(1)
    notification.mark_read(1)

    assert notification.read_by == [1]
    notification.refresh_from_db()
    assert notification.read_by == [1]


def test_mark_read_accumulates_distinct_readers(production):
    notification = make_insite()

    notification.mark_read(42)
    notification.mark_read(43)

    notification.refresh_from_db()
    assert notification.read_by == [42, 43]


def test_is_read_by_reflects_the_list(production):
    notification = make_insite()

    assert notification.is_read_by(42) is False
    notification.mark_read(42)
    assert notification.is_read_by(42) is True
    assert notification.is_read_by(43) is False


def test_mark_read_accepts_a_string_id(production):
    """Recipients are strings for in-site; readers are integers."""
    notification = make_insite()

    notification.mark_read("42")

    assert notification.read_by == [42]
    assert notification.is_read_by("42") is True


def test_read_state_survives_a_reload_from_another_instance(production):
    notification = make_insite()

    Notification.objects.get(pk=notification.pk).mark_read(42)

    notification.refresh_from_db()
    assert notification.is_read_by(42) is True


def test_unread_for_a_user_is_expressible_even_though_it_is_unindexed(production):
    """The negation query works; it is simply not indexed (PRD 6.4.1)."""
    read = make_insite(recipients=["42"])
    unread = make_insite(recipients=["42"])
    read.mark_read(42)

    pending = Notification.objects.filter(
        channel=Channel.INSITE, recipients__contains=["42"]
    ).exclude(read_by__contains=[42])

    assert list(pending.values_list("pk", flat=True)) == [unread.pk]


def test_insite_is_exempt_from_environment_gating(settings):
    """Gating in-site would protect nobody and break local development.

    The gate exists to stop a non-production system contacting real people.
    In-site contacts nobody, and the row is persisted before dispatch, so
    suppressing the send cannot un-write it.
    """
    settings.DJANGO_ENV = "dev"

    notification = make_insite(recipients=["42", "43"])

    log = notification.logs.get()
    assert log.result == LogResult.SENT
    assert log.effective_recipients == ["42", "43"]
    notification.refresh_from_db()
    assert notification.status == Status.SENT


def test_insite_ignores_a_default_recipient_even_if_one_exists(settings):
    from notifier.models import DefaultRecipient

    settings.DJANGO_ENV = "dev"
    DefaultRecipient.objects.create(channel=Channel.INSITE, address="999", enabled=True)

    notification = make_insite(recipients=["42"])

    assert notification.logs.get().effective_recipients == ["42"]


def test_email_and_sms_are_still_gated(settings, make_notification):
    """The exemption is in-site only."""
    settings.DJANGO_ENV = "dev"

    notification = make_notification(status=Status.READY, recipients=["real@example.com"])

    assert notification.logs.get().result == LogResult.SUPPRESSED
    assert mail.outbox == []

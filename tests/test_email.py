"""AC-17, AC-18, AC-29, AC-30: email delivery shape."""

import pytest
from django.core import mail

from notifier.models import Channel, LogResult, Notification, Status

pytestmark = pytest.mark.django_db

THREE = ["a@example.com", "b@example.com", "c@example.com"]


def test_ac_17_empty_recipients_is_skipped(production):
    notification = Notification.objects.create(
        channel=Channel.EMAIL,
        status=Status.READY,
        recipients=[],
        subject="Hello",
        body_text="Body.",
    )

    assert mail.outbox == []
    log = notification.logs.get()
    assert log.result == LogResult.SKIPPED


def test_ac_18_html_arrives_as_an_alternative(production):
    Notification.objects.create(
        channel=Channel.EMAIL,
        status=Status.READY,
        recipients=["to@example.com"],
        subject="Hello",
        body_text="Plain body.",
        body_html="<p>Rich body.</p>",
    )

    message = mail.outbox[0]
    assert message.body == "Plain body."
    assert message.alternatives[0][0] == "<p>Rich body.</p>"
    assert message.alternatives[0][1] == "text/html"


def test_html_only_gets_a_plaintext_alternative(production):
    Notification.objects.create(
        channel=Channel.EMAIL,
        status=Status.READY,
        recipients=["to@example.com"],
        subject="Hello",
        body_html="<p>Rich <b>body</b>.</p>",
    )

    message = mail.outbox[0]
    assert message.body == "Rich body."


def test_ac_40_a_single_recipient_goes_in_to(production):
    """With one recipient there is nobody to leak to, so the mail looks normal.

    Verified against a real provider: a Bcc-only envelope arrives with an empty
    "To", which reads as suspicious for ordinary transactional mail.
    """
    notification = Notification.objects.create(
        channel=Channel.EMAIL,
        status=Status.READY,
        recipients=["only@example.com"],
        subject="Hello",
        body_text="Body.",
    )

    assert mail.outbox[0].to == ["only@example.com"]
    assert mail.outbox[0].bcc == []
    assert notification.logs.get().provider_response["mode"] == "to"


@pytest.mark.parametrize(
    ("count", "expected"),
    [(0, "to"), (1, "to"), (2, "bcc"), (50, "bcc")],
)
def test_ac_40_auto_switches_at_two_recipients(count, expected):
    from notifier.backends.email import DjangoEmailBackend

    assert DjangoEmailBackend.resolve_mode("auto", count) == expected


@pytest.mark.parametrize("mode", ["to", "bcc", "separate"])
def test_ac_40_an_explicit_mode_is_never_overridden(mode):
    from notifier.backends.email import DjangoEmailBackend

    assert DjangoEmailBackend.resolve_mode(mode, 1) == mode
    assert DjangoEmailBackend.resolve_mode(mode, 9) == mode


def test_ac_29_three_addresses_are_one_bcc_message(production):
    notification = Notification.objects.create(
        channel=Channel.EMAIL,
        status=Status.READY,
        recipients=THREE,
        subject="Hello",
        body_text="Body.",
    )

    assert len(mail.outbox) == 1
    assert mail.outbox[0].bcc == THREE
    assert mail.outbox[0].to == []
    assert notification.logs.count() == 1
    assert notification.logs.get().provider_response["mode"] == "bcc"


def test_ac_30_to_mode_puts_everyone_in_to(production, settings):
    settings.NOTIFIER = {**settings.NOTIFIER, "EMAIL": {"RECIPIENT_MODE": "to"}}
    Notification.objects.create(
        channel=Channel.EMAIL,
        status=Status.READY,
        recipients=THREE,
        subject="Hello",
        body_text="Body.",
    )

    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == THREE
    assert mail.outbox[0].bcc == []


def test_ac_30_separate_mode_is_one_message_each_but_one_log(production, settings):
    settings.NOTIFIER = {**settings.NOTIFIER, "EMAIL": {"RECIPIENT_MODE": "separate"}}
    notification = Notification.objects.create(
        channel=Channel.EMAIL,
        status=Status.READY,
        recipients=THREE,
        subject="Hello",
        body_text="Body.",
    )

    assert len(mail.outbox) == 3
    assert [m.to for m in mail.outbox] == [[a] for a in THREE]
    assert notification.logs.count() == 1

    response = notification.logs.get().provider_response
    assert response["messages"] == 3
    assert set(response["per_recipient"]) == set(THREE)


def test_invalid_recipient_mode_is_rejected(production, settings, make_notification):
    from django.core.exceptions import ImproperlyConfigured

    settings.NOTIFIER = {**settings.NOTIFIER, "EMAIL": {"RECIPIENT_MODE": "nonsense"}}
    notification = make_notification(status=Status.DRAFT)

    with pytest.raises(ImproperlyConfigured, match="RECIPIENT_MODE"):
        notification.mark_ready()


def test_from_email_defaults_to_django_setting(production):
    Notification.objects.create(
        channel=Channel.EMAIL,
        status=Status.READY,
        recipients=["to@example.com"],
        subject="Hello",
        body_text="Body.",
    )

    assert mail.outbox[0].from_email == "notifier@example.com"

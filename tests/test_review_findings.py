"""Regressions for defects found by adversarial review of the 0.1.0 PR.

Each of these reproduced against real code before the fix landed. They live
together so the reasons stay visible; the AC ids are in PRD section 8.
"""

import smtplib
from io import StringIO

import pytest
from django.core import mail, serializers
from django.core.management import call_command

from notifier.backends.email import DjangoEmailBackend
from notifier.html2text import html_to_text
from notifier.models import Channel, LogResult, Notification, Status

# -- AC-41: loaddata must not send -------------------------------------------


@pytest.mark.django_db
def test_ac_41_loading_a_fixture_does_not_send(production, make_notification):
    """Restoring a backup must not re-deliver its contents.

    Django's deserializer saves with raw=True. Without a guard the post_save
    receiver treats a restored `ready` row as new work and mails it for real.
    """
    original = make_notification(status=Status.READY, recipients=["to@example.com"])
    payload = serializers.serialize("json", [original])
    Notification.objects.all().delete()
    mail.outbox.clear()

    for obj in serializers.deserialize("json", payload):
        obj.save()

    assert Notification.objects.count() == 1
    assert mail.outbox == []
    assert Notification.objects.get().logs.count() == 0


@pytest.mark.django_db
def test_ac_41_loaddata_command_does_not_send(production, make_notification, tmp_path):
    original = make_notification(status=Status.READY, recipients=["to@example.com"])
    fixture = tmp_path / "notifications.json"
    fixture.write_text(serializers.serialize("json", [original]))
    Notification.objects.all().delete()
    mail.outbox.clear()

    call_command("loaddata", str(fixture), verbosity=0)

    assert Notification.objects.count() == 1
    assert mail.outbox == []


@pytest.mark.django_db
def test_ac_41_a_normal_save_still_sends(production, make_notification):
    """The raw guard must not disarm the ordinary path."""
    notification = make_notification(status=Status.DRAFT)

    notification.mark_ready()

    assert len(mail.outbox) == 1


# -- AC-42: the database check is scoped to notifier's own alias -------------


@pytest.mark.django_db
def test_ac_42_an_unrelated_non_postgres_alias_is_not_flagged(settings):
    """A legacy replica notifier never touches must not block every command."""
    from notifier.checks import check_database_backend

    settings.DATABASES = {
        **settings.DATABASES,
        "legacy": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"},
    }

    assert check_database_backend(None) == []


@pytest.mark.django_db
def test_ac_42_the_routed_alias_is_still_checked(settings, monkeypatch):
    from django.db import connections

    from notifier.checks import check_database_backend

    monkeypatch.setattr(type(connections["default"]), "vendor", "sqlite", raising=False)
    errors = check_database_backend(None)

    assert [e.id for e in errors] == ["notifier.E002"]
    assert "holds notifier's tables" in errors[0].msg


# -- AC-43: self-closing skip tags -------------------------------------------


@pytest.mark.parametrize("tag", ["style", "title", "script", "noscript", "head"])
def test_ac_43_a_self_closing_skip_tag_does_not_eat_the_document(tag):
    """`<style/>` opened a skip region that nothing ever closed."""
    result = html_to_text(f"<p>before</p><{tag}/><p>after</p>")

    assert "before" in result
    assert "after" in result


def test_ac_43_a_self_closing_pre_does_not_disable_whitespace_collapsing():
    assert html_to_text("<pre/><p>a  b</p>") == "a b"


def test_ac_43_paired_skip_tags_still_skip():
    assert html_to_text("<p>a</p><style>p{color:red}</style><p>b</p>") == "a\n\nb"


# -- AC-44: separate mode participates in retries ----------------------------


class _AllFailing(DjangoEmailBackend):
    def _build(self, message, from_email, connection):
        email = super()._build(message, from_email, connection)
        email.send = lambda *a, **kw: (_ for _ in ()).throw(smtplib.SMTPServerDisconnected("down"))
        return email


class _PartiallyFailing(DjangoEmailBackend):
    def _build(self, message, from_email, connection):
        email = super()._build(message, from_email, connection)
        original = email.send

        def send(*args, **kwargs):
            if email.to == ["b@example.com"]:
                raise smtplib.SMTPServerDisconnected("down")
            return original(*args, **kwargs)

        email.send = send
        return email


@pytest.mark.django_db
def test_ac_44_a_total_outage_in_separate_mode_reraises(production, settings, make_notification):
    """`to` and `bcc` got retries for free; `separate` silently opted out."""
    settings.NOTIFIER = {
        **settings.NOTIFIER,
        "EMAIL": {"RECIPIENT_MODE": "separate"},
        "BACKENDS": {**settings.NOTIFIER["BACKENDS"], "email": f"{__name__}._AllFailing"},
    }
    from notifier.dispatch import deliver

    notification = make_notification(
        status=Status.DRAFT, recipients=["a@example.com", "b@example.com"]
    )

    with pytest.raises(smtplib.SMTPServerDisconnected):
        deliver(notification)

    notification.refresh_from_db()
    assert notification.status == Status.FAILED
    assert notification.logs.get().result == LogResult.FAILED


@pytest.mark.django_db
def test_ac_44_a_partial_success_is_never_retried(production, settings, make_notification):
    """Retrying would send a second copy to the address that already got one."""
    settings.NOTIFIER = {
        **settings.NOTIFIER,
        "EMAIL": {"RECIPIENT_MODE": "separate"},
        "BACKENDS": {**settings.NOTIFIER["BACKENDS"], "email": f"{__name__}._PartiallyFailing"},
    }
    from notifier.dispatch import deliver

    notification = make_notification(
        status=Status.DRAFT, recipients=["a@example.com", "b@example.com"]
    )

    log = deliver(notification)

    assert log.result == LogResult.FAILED
    assert log.provider_response["messages"] == 1
    assert len(mail.outbox) == 1


# -- AC-45: one failed enqueue must not strand its siblings ------------------


@pytest.mark.django_db(transaction=True)
def test_ac_45_one_channels_broken_backend_does_not_strand_the_others(production, settings):
    """`send_multi` promises one channel failing neither blocks nor rolls back
    the others. Django clears the on-commit hook list before running it, so
    without `robust=True` the SMS row's ImproperlyConfigured discarded the email
    row's still-pending dispatch -- silently, with no log row to show for it.
    """
    from notifier import send_multi

    settings.NOTIFIER = {
        **settings.NOTIFIER,
        "DISPATCH_ON_COMMIT": True,
        "BACKENDS": {
            **settings.NOTIFIER["BACKENDS"],
            "sms": "notifier.backends.nonexistent.Missing",
        },
    }

    try:
        send_multi(
            recipients={Channel.SMS: ["+6421234567"], Channel.EMAIL: ["second@example.com"]},
            subject="Multi",
            body_text="Body.",
            source="test.multi",
        )

        email_row = Notification.objects.get(source="test.multi", channel=Channel.EMAIL)
        assert email_row.status == Status.SENT, "the SMS backend's failure stranded the email"
        assert [m.to for m in mail.outbox] == [["second@example.com"]]
    finally:
        Notification.objects.filter(source="test.multi").delete()


@pytest.mark.django_db
def test_ac_45_a_broker_outage_does_not_escape_into_the_callers_save(
    production, settings, make_notification, monkeypatch
):
    """apply_async sat outside the try/except, so a broker outage propagated out
    of the enclosing save() and took every sibling hook with it."""
    from notifier import dispatch as dispatch_module

    settings.NOTIFIER = {**settings.NOTIFIER, "USE_CELERY": True}

    class _DeadBroker:
        @staticmethod
        def apply_async(*args, **kwargs):
            raise OSError("redis is down")

    monkeypatch.setattr("notifier.tasks.send_notification_task", _DeadBroker, raising=False)

    notification = make_notification(status=Status.DRAFT)
    notification.mark_ready()  # must not raise

    notification.refresh_from_db()
    assert notification.status == Status.READY
    assert notification.logs.count() == 0
    assert dispatch_module.use_celery() is True


# -- coverage gaps the reviewers flagged -------------------------------------


@pytest.mark.django_db
def test_the_suppression_opt_out_actually_opts_out(settings, make_notification):
    """`SUPPRESS_WHEN_NO_DEFAULT_RECIPIENT=False` is the only switch that lets a
    non-production environment reach real people. Both branches need pinning."""
    settings.DJANGO_ENV = "dev"
    settings.NOTIFIER = {**settings.NOTIFIER, "SUPPRESS_WHEN_NO_DEFAULT_RECIPIENT": False}
    notification = make_notification(status=Status.DRAFT, recipients=["real@example.com"])

    notification.mark_ready()

    log = notification.logs.get()
    assert log.result == LogResult.SENT
    assert log.effective_recipients == ["real@example.com"]
    assert mail.outbox[0].to == ["real@example.com"]


@pytest.mark.django_db
def test_the_three_quiet_signals_actually_fire(production, settings, make_notification):
    """pre_send, notification_sent and notification_suppressed had no assertions
    anywhere, so all three could have been deleted with a green suite."""
    from notifier import signals

    seen = []
    receivers = {
        "pre_send": signals.pre_send,
        "notification_sent": signals.notification_sent,
        "notification_suppressed": signals.notification_suppressed,
    }
    handlers = {}
    for name, signal in receivers.items():
        handlers[name] = lambda sender, name=name, **kw: seen.append(name)
        signal.connect(handlers[name], sender=Notification)

    try:
        make_notification(status=Status.READY)
        assert seen == ["pre_send", "notification_sent"]

        seen.clear()
        settings.DJANGO_ENV = "dev"
        make_notification(status=Status.READY, recipients=["real@example.com"])
        assert seen == ["notification_suppressed"]
    finally:
        for name, signal in receivers.items():
            signal.disconnect(handlers[name], sender=Notification)


@pytest.mark.django_db
def test_console_sms_backend_writes_to_its_stream(production, settings, make_notification):
    """The shipped default SMS backend had no direct test."""
    from notifier.backends.sms import ConsoleSmsBackend

    stream = StringIO()
    ConsoleSmsBackend.stream = stream
    settings.NOTIFIER = {
        **settings.NOTIFIER,
        "BACKENDS": {
            **settings.NOTIFIER["BACKENDS"],
            "sms": "notifier.backends.sms.ConsoleSmsBackend",
        },
    }
    try:
        make_notification(
            status=Status.READY,
            channel=Channel.SMS,
            recipients=["+6421234567"],
            subject="",
            body_text="Console body.",
        )
        assert "+6421234567" in stream.getvalue()
        assert "Console body." in stream.getvalue()
    finally:
        ConsoleSmsBackend.stream = None

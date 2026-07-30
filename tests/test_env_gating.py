"""AC-06…AC-09, AC-37: the non-production safety net and env resolution."""

import pytest
from django.core import mail

from notifier.conf import notifier_settings
from notifier.models import Channel, DefaultRecipient, LogResult, Status

pytestmark = pytest.mark.django_db


def test_ac_06_non_prod_redirects_to_default_recipient(
    make_notification, default_email_recipient, settings
):
    settings.DJANGO_ENV = "dev"
    notification = make_notification(status=Status.DRAFT, recipients=["real@example.com"])

    notification.mark_ready()

    assert len(mail.outbox) == 1
    # One effective recipient, so "auto" resolves to `to`. Note the redirect is
    # what made it one -- the count that matters is the effective list.
    assert mail.outbox[0].to == ["qa@example.com"]
    assert mail.outbox[0].bcc == []

    log = notification.logs.get()
    assert log.result == LogResult.SENT
    assert log.requested_recipients == ["real@example.com"]
    assert log.effective_recipients == ["qa@example.com"]
    assert log.env == "dev"


def test_ac_07_non_prod_without_default_recipient_is_suppressed(make_notification, settings):
    settings.DJANGO_ENV = "dev"
    notification = make_notification(status=Status.DRAFT, recipients=["real@example.com"])

    notification.mark_ready()

    assert mail.outbox == []
    log = notification.logs.get()
    assert log.result == LogResult.SUPPRESSED
    assert log.requested_recipients == ["real@example.com"]
    assert log.effective_recipients == []

    # Status stays at ready: it never claimed to have been sent.
    notification.refresh_from_db()
    assert notification.status == Status.READY
    assert notification.sent_at is None


def test_ac_08_production_uses_real_recipients_without_prefix(make_notification, production):
    notification = make_notification(status=Status.DRAFT, recipients=["real@example.com"])

    notification.mark_ready()

    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["real@example.com"]
    assert mail.outbox[0].subject == "Hello"
    assert notification.logs.get().result == LogResult.SENT


def test_ac_09_disabled_default_recipient_is_ignored(make_notification, settings):
    settings.DJANGO_ENV = "dev"
    DefaultRecipient.objects.create(
        channel=Channel.EMAIL, address="disabled@example.com", enabled=False
    )
    notification = make_notification(status=Status.DRAFT, recipients=["real@example.com"])

    notification.mark_ready()

    assert mail.outbox == []
    assert notification.logs.get().result == LogResult.SUPPRESSED


def test_non_prod_subject_carries_env_prefix(make_notification, default_email_recipient, settings):
    settings.DJANGO_ENV = "staging"
    notification = make_notification(status=Status.DRAFT)

    notification.mark_ready()

    assert mail.outbox[0].subject == "[staging] Hello"


def test_ac_37_env_resolution_order(settings, monkeypatch):
    monkeypatch.setenv("DJANGO_ENV", "from-envvar")

    # 2. settings.DJANGO_ENV beats the environment variable.
    settings.DJANGO_ENV = "from-settings"
    settings.NOTIFIER = {**settings.NOTIFIER, "ENV": None}
    assert notifier_settings.env == "from-settings"

    # 1. NOTIFIER["ENV"] beats settings.DJANGO_ENV.
    settings.NOTIFIER = {**settings.NOTIFIER, "ENV": "from-notifier"}
    assert notifier_settings.env == "from-notifier"

    # 3. the environment variable applies when neither setting is present.
    settings.NOTIFIER = {**settings.NOTIFIER, "ENV": None}
    del settings.DJANGO_ENV
    assert notifier_settings.env == "from-envvar"


def test_ac_37_unconfigured_project_is_not_production(settings, monkeypatch, make_notification):
    monkeypatch.delenv("DJANGO_ENV", raising=False)
    settings.NOTIFIER = {**settings.NOTIFIER, "ENV": None}
    del settings.DJANGO_ENV

    # 4. the fail-safe fallback.
    assert notifier_settings.env == "dev"
    assert notifier_settings.is_production is False

    notification = make_notification(status=Status.DRAFT, recipients=["real@example.com"])
    notification.mark_ready()

    assert mail.outbox == []
    assert notification.logs.get().result == LogResult.SUPPRESSED

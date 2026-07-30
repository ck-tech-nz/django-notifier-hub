"""Shared fixtures.

Test names carry the acceptance-criterion id from PRD 8, so ``pytest -k AC-07``
runs exactly the test for that criterion.
"""

import pytest
from django.core import mail

from notifier.backends import sms
from notifier.models import Channel, DefaultRecipient, Notification, NotificationTemplate, Status


@pytest.fixture(autouse=True)
def _clean_outboxes():
    """Empty both outboxes around every test."""
    mail.outbox.clear()
    sms.outbox.clear()
    yield
    mail.outbox.clear()
    sms.outbox.clear()


@pytest.fixture
def production(settings):
    """Run the test as if in production: no recipient redirection."""
    settings.DJANGO_ENV = "production"
    return settings


@pytest.fixture
def default_email_recipient(db):
    return DefaultRecipient.objects.create(
        channel=Channel.EMAIL, address="qa@example.com", enabled=True, note="QA mailbox"
    )


@pytest.fixture
def email_template(db):
    return NotificationTemplate.objects.create(
        key="order-shipped",
        name="Order shipped",
        channel=Channel.EMAIL,
        subject="Order {{ order }} shipped",
        body_text="Hello, order {{ order }} is on its way.",
        body_html="<p>Hello, order <strong>{{ order }}</strong> is on its way.</p>",
    )


@pytest.fixture
def sms_template(db):
    return NotificationTemplate.objects.create(
        key="order-shipped",
        name="Order shipped (SMS)",
        channel=Channel.SMS,
        body_text="Order {{ order }} shipped.",
    )


@pytest.fixture
def make_notification(db):
    """Build a valid notification without dispatching it."""

    def factory(**overrides):
        fields = {
            "channel": Channel.EMAIL,
            "status": Status.DRAFT,
            "recipients": ["to@example.com"],
            "subject": "Hello",
            "body_text": "Body.",
        }
        fields.update(overrides)
        return Notification.objects.create(**fields)

    return factory

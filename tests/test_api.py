"""AC-38 plus the public convenience API."""

import pytest

from notifier import send
from notifier.models import Channel, LogResult, Notification, Status

pytestmark = pytest.mark.django_db


def test_send_creates_and_dispatches(production):
    notification = send(
        channel=Channel.EMAIL,
        recipients=["to@example.com"],
        subject="Hello",
        body_text="Body.",
    )

    notification.refresh_from_db()
    assert notification.status == Status.SENT
    assert notification.logs.get().result == LogResult.SENT


def test_send_with_a_template_key(email_template, production):
    notification = send(
        channel=Channel.EMAIL,
        recipients=["to@example.com"],
        key="order-shipped",
        context={"order": "A-7"},
    )

    notification.refresh_from_db()
    assert notification.template_id == email_template.pk
    assert notification.rendered_subject == "Order A-7 shipped"


def test_send_as_draft_does_not_dispatch(production):
    notification = send(
        channel=Channel.EMAIL,
        recipients=["to@example.com"],
        subject="Hello",
        body_text="Body.",
        status=Status.DRAFT,
    )

    assert notification.logs.count() == 0


def test_ac_38_source_is_persisted(production):
    notification = send(
        channel=Channel.EMAIL,
        recipients=["to@example.com"],
        subject="Hello",
        body_text="Body.",
        source="order.shipped",
    )

    notification.refresh_from_db()
    assert notification.source == "order.shipped"


def test_ac_38_source_is_optional(production):
    notification = send(
        channel=Channel.EMAIL,
        recipients=["to@example.com"],
        subject="Hello",
        body_text="Body.",
    )

    notification.refresh_from_db()
    assert notification.source == ""
    assert notification.status == Status.SENT


def test_ac_38_source_is_filterable(production):
    send(
        channel=Channel.EMAIL,
        recipients=["to@example.com"],
        subject="Hello",
        body_text="Body.",
        source="cron:daily-digest",
    )
    send(
        channel=Channel.EMAIL,
        recipients=["to@example.com"],
        subject="Hello",
        body_text="Body.",
        source="order.shipped",
    )

    assert Notification.objects.filter(source="cron:daily-digest").count() == 1


def test_send_validates_before_saving():
    from django.core.exceptions import ValidationError

    with pytest.raises(ValidationError):
        send(channel=Channel.EMAIL, recipients=["to@example.com"], subject="", body_text="")

    assert Notification.objects.count() == 0

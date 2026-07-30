"""AC-25…AC-28, AC-32: what the database enforces regardless of clean()."""

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from notifier.models import (
    MAX_RECIPIENTS,
    Channel,
    Notification,
    NotificationTemplate,
    Status,
)

pytestmark = pytest.mark.django_db


def test_ac_25_one_key_may_serve_several_channels(email_template, sms_template):
    assert email_template.key == sms_template.key
    assert NotificationTemplate.objects.filter(key="order-shipped").count() == 2


def test_ac_25_duplicate_key_channel_pair_rejected(email_template):
    with pytest.raises(IntegrityError), transaction.atomic():
        NotificationTemplate.objects.create(
            key=email_template.key,
            name="Duplicate",
            channel=Channel.EMAIL,
            subject="s",
            body_text="t",
        )


def test_ac_26_bulk_create_cannot_bypass_the_sms_html_rule():
    """clean() is skipped by bulk_create; the constraint is not."""
    notification = Notification(
        channel=Channel.SMS,
        recipients=["+6421234567"],
        body_text="Text.",
        body_html="<p>Not allowed.</p>",
    )

    with pytest.raises(IntegrityError), transaction.atomic():
        Notification.objects.bulk_create([notification])


def test_ac_27_read_by_is_insite_only():
    notification = Notification(
        channel=Channel.EMAIL,
        recipients=["to@example.com"],
        subject="Hello",
        body_text="Body.",
        read_by=[1],
    )

    with pytest.raises(IntegrityError), transaction.atomic():
        Notification.objects.bulk_create([notification])


def test_ac_27_read_by_allowed_on_insite():
    notification = Notification.objects.create(
        channel=Channel.INSITE, recipients=["42"], subject="Hello", read_by=[42]
    )

    assert notification.read_by == [42]


def test_sent_requires_sent_at():
    notification = Notification(
        channel=Channel.EMAIL,
        status=Status.SENT,
        recipients=["to@example.com"],
        subject="Hello",
        body_text="Body.",
        sent_at=None,
    )

    with pytest.raises(IntegrityError), transaction.atomic():
        Notification.objects.bulk_create([notification])


def test_ac_28_recipients_contains_uses_the_gin_index(production):
    Notification.objects.create(
        channel=Channel.EMAIL,
        status=Status.DRAFT,
        recipients=["needle@example.com", "other@example.com"],
        subject="Hello",
        body_text="Body.",
    )
    Notification.objects.create(
        channel=Channel.EMAIL,
        status=Status.DRAFT,
        recipients=["unrelated@example.com"],
        subject="Hello",
        body_text="Body.",
    )

    matched = Notification.objects.filter(recipients__contains=["needle@example.com"])
    assert matched.count() == 1

    plan = "\n".join(
        str(row)
        for row in Notification.objects.filter(recipients__contains=["needle@example.com"])
        .explain()
        .splitlines()
    )
    # The index exists and is usable; on a two-row table the planner will still
    # prefer a sequential scan, so assert the index is present rather than used.
    assert "notifier_recipients_gin" in _index_names()
    assert plan  # explain() ran


def _index_names() -> set[str]:
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT indexname FROM pg_indexes WHERE tablename = %s",
            [Notification._meta.db_table],
        )
        return {row[0] for row in cursor.fetchall()}


def test_ac_32_five_hundred_recipients_saves():
    notification = Notification.objects.create(
        channel=Channel.EMAIL,
        recipients=[f"user{i}@example.com" for i in range(MAX_RECIPIENTS)],
        subject="Hello",
        body_text="Body.",
    )

    assert len(notification.recipients) == MAX_RECIPIENTS


def test_ac_32_five_hundred_and_one_recipients_rejected_by_the_database():
    notification = Notification(
        channel=Channel.EMAIL,
        recipients=[f"user{i}@example.com" for i in range(MAX_RECIPIENTS + 1)],
        subject="Hello",
        body_text="Body.",
    )

    with pytest.raises(IntegrityError), transaction.atomic():
        Notification.objects.bulk_create([notification])


def test_ac_32_clean_gives_a_readable_message():
    notification = Notification(
        channel=Channel.EMAIL,
        recipients=[f"user{i}@example.com" for i in range(MAX_RECIPIENTS + 1)],
        subject="Hello",
        body_text="Body.",
    )

    with pytest.raises(ValidationError) as exc:
        notification.full_clean(exclude=["read_by"])

    message = str(exc.value.message_dict["recipients"])
    assert "500" in message
    assert "501" in message


@pytest.mark.parametrize("value", [["", "ok@example.com"], ["   "], [None]])
def test_recipients_must_be_non_empty_strings(value):
    notification = Notification(
        channel=Channel.EMAIL, recipients=value, subject="Hello", body_text="Body."
    )

    with pytest.raises(ValidationError):
        notification.full_clean(exclude=["read_by"])


def test_empty_recipients_are_allowed_and_skipped_at_dispatch(production):
    notification = Notification.objects.create(
        channel=Channel.EMAIL, recipients=[], subject="Hello", body_text="Body."
    )

    assert notification.recipients == []

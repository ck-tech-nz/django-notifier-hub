"""AC-21…AC-24: one message over several channels."""

import uuid

import pytest
from django.core import mail
from django.core.exceptions import ValidationError

from notifier import send_multi
from notifier.backends import sms
from notifier.models import Channel, LogResult, Notification, NotificationTemplate, Status

pytestmark = pytest.mark.django_db

ALL_THREE = {
    Channel.EMAIL: ["ops@example.com"],
    Channel.SMS: ["+6421000001"],
    Channel.INSITE: ["42"],
}


@pytest.fixture
def insite_template(db):
    return NotificationTemplate.objects.create(
        key="order-shipped",
        name="Order shipped (in-site)",
        channel=Channel.INSITE,
        subject="Order {{ order }} shipped",
        body_html="<p>Order {{ order }} is on its way.</p>",
    )


@pytest.fixture
def all_templates(email_template, sms_template, insite_template):
    return email_template, sms_template, insite_template


def test_ac_21_three_channels_are_three_rows_sharing_one_group(all_templates, production):
    group_id = send_multi(key="order-shipped", recipients=ALL_THREE, context={"order": "A-1"})

    siblings = Notification.objects.filter(group_id=group_id)
    assert siblings.count() == 3
    assert {n.channel for n in siblings} == {Channel.EMAIL, Channel.SMS, Channel.INSITE}
    assert {n.status for n in siblings} == {Status.SENT}
    assert sum(n.logs.count() for n in siblings) == 3
    assert isinstance(group_id, uuid.UUID)

    # Each channel resolved its own template from the one logical key.
    assert len(mail.outbox) == 1
    assert len(sms.outbox) == 1
    assert mail.outbox[0].subject == "Order A-1 shipped"
    assert sms.outbox[0]["text"] == "Order A-1 shipped."


def test_a_single_channel_send_has_no_group(production):
    from notifier import send

    notification = send(
        channel=Channel.EMAIL, recipients=["to@example.com"], subject="Hi", body_text="Body."
    )

    assert notification.group_id is None


def test_ac_22_one_channel_failing_leaves_its_siblings_sent(all_templates, production, settings):
    settings.NOTIFIER = {
        **settings.NOTIFIER,
        "BACKENDS": {
            **settings.NOTIFIER["BACKENDS"],
            "sms": "notifier.backends.sms.FailingSmsBackend",
        },
    }

    group_id = send_multi(key="order-shipped", recipients=ALL_THREE, context={"order": "A-1"})

    by_channel = {n.channel: n for n in Notification.objects.filter(group_id=group_id)}
    # No rollback, no shared status: each lifecycle is independent.
    assert by_channel[Channel.SMS].status == Status.FAILED
    assert by_channel[Channel.EMAIL].status == Status.SENT
    assert by_channel[Channel.INSITE].status == Status.SENT
    assert len(mail.outbox) == 1


def test_ac_23_a_missing_template_creates_zero_rows(email_template, sms_template, production):
    """No in-site template exists, so the whole group rolls back."""
    with pytest.raises(ValidationError, match=r"insite"):
        send_multi(key="order-shipped", recipients=ALL_THREE, context={"order": "A-1"})

    assert Notification.objects.count() == 0
    assert mail.outbox == []


def test_ac_23_require_all_templates_false_skips_absent_channels(
    email_template, sms_template, production
):
    group_id = send_multi(
        key="order-shipped",
        recipients=ALL_THREE,
        context={"order": "A-1"},
        require_all_templates=False,
    )

    siblings = Notification.objects.filter(group_id=group_id)
    assert {n.channel for n in siblings} == {Channel.EMAIL, Channel.SMS}
    assert siblings.count() == 2


def test_ac_24_gating_applies_per_channel(all_templates, settings, default_email_recipient):
    settings.DJANGO_ENV = "dev"

    group_id = send_multi(key="order-shipped", recipients=ALL_THREE, context={"order": "A-1"})

    by_channel = {n.channel: n for n in Notification.objects.filter(group_id=group_id)}

    # Email has a default recipient: redirected.
    email_log = by_channel[Channel.EMAIL].logs.get()
    assert email_log.result == LogResult.SENT
    assert email_log.effective_recipients == ["qa@example.com"]

    # SMS has none: suppressed, and its status stays at ready.
    sms_log = by_channel[Channel.SMS].logs.get()
    assert sms_log.result == LogResult.SUPPRESSED
    assert by_channel[Channel.SMS].status == Status.READY
    assert sms.outbox == []

    # In-site is exempt from gating entirely.
    assert by_channel[Channel.INSITE].logs.get().result == LogResult.SENT

    # A group legitimately ends up with mixed results outside production.
    assert {n.status for n in by_channel.values()} == {Status.SENT, Status.READY}


def test_source_and_context_are_shared_across_the_group(all_templates, production):
    group_id = send_multi(
        key="order-shipped",
        recipients=ALL_THREE,
        context={"order": "A-1"},
        source="order.shipped",
    )

    siblings = Notification.objects.filter(group_id=group_id)
    assert {n.source for n in siblings} == {"order.shipped"}
    assert {n.context["order"] for n in siblings} == {"A-1"}


def test_inline_bodies_work_without_any_template(production):
    group_id = send_multi(
        recipients={Channel.EMAIL: ["ops@example.com"], Channel.SMS: ["+6421000001"]},
        subject="Stock alert",
        body_text="SKU-9 is below the reorder level.",
        body_html="<p>SKU-9 is below the reorder level.</p>",
    )

    by_channel = {n.channel: n for n in Notification.objects.filter(group_id=group_id)}
    assert by_channel[Channel.EMAIL].rendered_html.startswith("<p>")
    # SMS cannot carry HTML, so the inline HTML body is dropped for it rather
    # than failing the whole group on a check constraint.
    assert by_channel[Channel.SMS].body_html == ""
    assert by_channel[Channel.SMS].rendered_html == ""


def test_send_multi_needs_at_least_one_channel():
    with pytest.raises(ValidationError, match="at least one channel"):
        send_multi(recipients={}, subject="Hi", body_text="Body.")


def test_send_multi_can_stage_a_group_as_draft(all_templates, production):
    group_id = send_multi(
        key="order-shipped",
        recipients=ALL_THREE,
        context={"order": "A-1"},
        status=Status.DRAFT,
    )

    siblings = Notification.objects.filter(group_id=group_id)
    assert {n.status for n in siblings} == {Status.DRAFT}
    assert mail.outbox == []

    # Releasing the group is a loop over the siblings; no special API needed.
    for notification in siblings:
        notification.mark_ready()
    assert len(mail.outbox) == 1


def test_a_validation_error_in_one_channel_rolls_the_whole_group_back(production):
    """SMS has no text body here, so the group must not be half-created."""
    with pytest.raises(ValidationError):
        send_multi(
            recipients={Channel.EMAIL: ["ops@example.com"], Channel.SMS: ["+6421000001"]},
            subject="Subject only",
            body_html="<p>HTML only.</p>",
        )

    assert Notification.objects.count() == 0
    assert mail.outbox == []

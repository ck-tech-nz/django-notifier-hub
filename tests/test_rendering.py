"""AC-10…AC-13, AC-33, AC-34: template resolution and validation."""

import pytest
from django.core.exceptions import ValidationError

from notifier.models import Channel, Notification, NotificationTemplate, Status
from notifier.rendering import render_string

pytestmark = pytest.mark.django_db


def test_ac_10_template_and_context_render(email_template, production):
    notification = Notification.objects.create(
        channel=Channel.EMAIL,
        status=Status.READY,
        recipients=["to@example.com"],
        template=email_template,
        context={"order": "A-1001"},
    )

    notification.refresh_from_db()
    assert notification.rendered_subject == "Order A-1001 shipped"
    assert notification.rendered_text == "Hello, order A-1001 is on its way."
    assert "<strong>A-1001</strong>" in notification.rendered_html


def test_ac_11_inline_subject_overrides_template(email_template, production):
    notification = Notification.objects.create(
        channel=Channel.EMAIL,
        status=Status.READY,
        recipients=["to@example.com"],
        template=email_template,
        context={"order": "A-1001"},
        subject="Explicit subject",
    )

    notification.refresh_from_db()
    assert notification.rendered_subject == "Explicit subject"
    # The body still comes from the template.
    assert notification.rendered_text == "Hello, order A-1001 is on its way."


def test_ac_12_editing_a_template_does_not_rewrite_history(email_template, production):
    notification = Notification.objects.create(
        channel=Channel.EMAIL,
        status=Status.READY,
        recipients=["to@example.com"],
        template=email_template,
        context={"order": "A-1001"},
    )
    notification.refresh_from_db()
    original = notification.rendered_text

    email_template.body_text = "Completely different wording."
    email_template.save()

    notification.refresh_from_db()
    assert notification.rendered_text == original


def test_deleting_a_template_keeps_the_notification(email_template, production):
    notification = Notification.objects.create(
        channel=Channel.EMAIL,
        status=Status.READY,
        recipients=["to@example.com"],
        template=email_template,
        context={"order": "A-1001"},
    )

    email_template.delete()

    notification.refresh_from_db()
    assert notification.template_id is None
    assert notification.rendered_subject == "Order A-1001 shipped"


@pytest.mark.parametrize(
    ("fields", "expected_field"),
    [
        ({"channel": Channel.EMAIL, "subject": "", "body_text": "x"}, "subject"),
        ({"channel": Channel.EMAIL, "subject": "s", "body_text": "", "body_html": ""}, "body_text"),
        ({"channel": Channel.SMS, "body_text": ""}, "body_text"),
        ({"channel": Channel.SMS, "body_text": "x", "body_html": "<p>x</p>"}, "body_html"),
        ({"channel": Channel.INSITE, "subject": "", "body_text": "", "body_html": ""}, "subject"),
    ],
)
def test_ac_13_channel_body_validation(fields, expected_field):
    notification = Notification(recipients=["to@example.com"], **fields)

    with pytest.raises(ValidationError) as exc:
        notification.full_clean(exclude=["read_by"])

    assert expected_field in exc.value.message_dict


def test_ac_13_template_channel_must_match(email_template):
    notification = Notification(
        channel=Channel.SMS, recipients=["+6421234567"], template=email_template
    )

    with pytest.raises(ValidationError) as exc:
        notification.full_clean(exclude=["read_by"])

    assert "template" in exc.value.message_dict


def test_ac_13_inactive_template_rejected(email_template):
    email_template.is_active = False
    email_template.save()
    notification = Notification(
        channel=Channel.EMAIL, recipients=["to@example.com"], template=email_template
    )

    with pytest.raises(ValidationError) as exc:
        notification.full_clean(exclude=["read_by"])

    assert "template" in exc.value.message_dict


def test_ac_33_full_engine_semantics(production):
    """The full Django engine: loops, filters, autoescaping."""
    source = "{% for n in names %}{{ n|upper }} {% endfor %}|{{ danger }}"
    context = {"names": ["ann", "bo"], "danger": "<script>"}

    rendered = render_string(source, context)

    assert rendered.startswith("ANN BO ")
    # Autoescaping is on, exactly as in a TemplateResponse.
    assert "&lt;script&gt;" in rendered
    assert "<script>" not in rendered


def test_ac_33_no_request_in_context(production):
    """There is no request, so request-dependent variables render empty."""
    assert render_string("[{{ request }}][{{ user }}]", {}) == "[][]"


def test_ac_34_template_syntax_error_caught_at_edit_time():
    template = NotificationTemplate(
        key="broken",
        name="Broken",
        channel=Channel.EMAIL,
        subject="Fine",
        body_text="{% if unclosed %}",
    )

    with pytest.raises(ValidationError) as exc:
        template.full_clean()

    assert "body_text" in exc.value.message_dict
    assert "syntax" in str(exc.value.message_dict["body_text"]).lower()


def test_template_context_processor_is_merged_under_context(settings, production):
    settings.NOTIFIER = {
        **settings.NOTIFIER,
        "TEMPLATE_CONTEXT_PROCESSOR": "tests.test_rendering.extra_context",
    }
    notification = Notification.objects.create(
        channel=Channel.EMAIL,
        status=Status.READY,
        recipients=["to@example.com"],
        subject="{{ site_url }}",
        body_text="{{ site_url }} {{ order }}",
        context={"order": "A-1"},
    )

    notification.refresh_from_db()
    assert notification.rendered_subject == "https://app.example.com"
    assert notification.rendered_text == "https://app.example.com A-1"


def extra_context():
    return {"site_url": "https://app.example.com"}

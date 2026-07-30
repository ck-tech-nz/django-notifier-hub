"""Admin registration.

Only meaningful when ``django.contrib.admin`` is installed, so the whole module
is skipped under the headless settings -- which is itself part of the point.
"""

import pytest
from django.core import mail

from notifier.models import (
    DefaultRecipient,
    Notification,
    NotificationLog,
    NotificationTemplate,
    Status,
)


def _admin_installed() -> bool:
    """`django.contrib.admin` is importable even when it is not installed, so
    `importorskip` is the wrong test -- ask the app registry instead."""
    from django.apps import apps

    return apps.is_installed("django.contrib.admin")


pytestmark = [
    pytest.mark.django_db,
    pytest.mark.skipif(
        not _admin_installed(),
        reason="admin is not installed; the headless configuration never loads admin.py (AC-35)",
    ),
]


@pytest.fixture
def site():
    from django.contrib import admin

    return admin.site


def test_all_four_models_are_registered(site):
    for model in (Notification, NotificationTemplate, NotificationLog, DefaultRecipient):
        assert site.is_registered(model), model


def test_the_log_is_read_only_everywhere(site):
    log_admin = site._registry[NotificationLog]

    assert log_admin.has_add_permission(None) is False
    assert log_admin.has_change_permission(None) is False
    # Append-only: every field is readonly, so nothing can be edited after the
    # fact and the audit trail stays trustworthy.
    assert set(log_admin.get_readonly_fields(None)) == {
        field.name for field in NotificationLog._meta.fields
    }


def test_the_log_inline_cannot_be_added_to(site):
    notification_admin = site._registry[Notification]
    inline = notification_admin.inlines[0](Notification, site)

    assert inline.has_add_permission(None, None) is False


def test_rendered_snapshots_are_read_only(site):
    readonly = set(site._registry[Notification].readonly_fields)

    assert {"rendered_subject", "rendered_text", "rendered_html", "send_attempts"} <= readonly


def test_resend_action_redispatches(site, make_notification, production, rf):
    from django.contrib.messages.storage.fallback import FallbackStorage

    notification = make_notification(status=Status.DRAFT)
    assert notification.logs.count() == 0

    request = rf.post("/admin/notifier/notification/")
    request.session = {}
    request._messages = FallbackStorage(request)

    notification_admin = site._registry[Notification]
    notification_admin.resend_selected(request, Notification.objects.filter(pk=notification.pk))

    assert notification.logs.count() == 1
    assert len(mail.outbox) == 1


def _resend_url(notification):
    from django.urls import reverse

    return reverse("admin:notifier_notification_resend", args=[notification.pk])


def test_ac_46_the_changelist_carries_a_resend_button_per_row(admin_client, make_notification):
    notification = make_notification()

    response = admin_client.get("/admin/notifier/notification/")
    body = response.content.decode()

    # A submit button, not a link: it rides the changelist's own form.
    assert f'<input type="submit" value="Re-send" formaction="{_resend_url(notification)}">' in body


def test_ac_46_the_change_form_carries_a_resend_button(admin_client, make_notification):
    notification = make_notification()

    response = admin_client.get(f"/admin/notifier/notification/{notification.pk}/change/")

    assert f'formaction="{_resend_url(notification)}"' in response.content.decode()


def test_ac_46_pressing_it_redispatches_and_returns_to_the_referring_page(
    admin_client, make_notification, production
):
    notification = make_notification(status=Status.DRAFT)
    changelist = "/admin/notifier/notification/?status__exact=draft"

    response = admin_client.post(_resend_url(notification), headers={"referer": changelist})

    assert response.status_code == 302
    # Back to the filtered changelist, not to a bare list.
    assert response["Location"] == changelist
    assert notification.logs.count() == 1
    assert len(mail.outbox) == 1


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({}, id="no-referer"),
        pytest.param({"referer": "https://evil.example.com/admin/"}, id="off-site-referer"),
    ],
)
def test_ac_46_without_a_usable_referer_it_falls_back_to_the_change_page(
    admin_client, make_notification, production, headers
):
    """A browser sending `Referrer-Policy: no-referrer` still gets somewhere
    sensible, and an off-site referer is never redirected to."""
    notification = make_notification()

    response = admin_client.post(_resend_url(notification), headers=headers)

    assert response["Location"] == f"/admin/notifier/notification/{notification.pk}/change/"
    assert len(mail.outbox) == 1


def test_ac_46_resending_a_deleted_notification_is_a_404(admin_client, production):
    response = admin_client.post("/admin/notifier/notification/12345/resend/")

    assert response.status_code == 404
    assert mail.outbox == []


def test_ac_46_a_get_on_the_resend_url_sends_nothing(admin_client, make_notification, production):
    """The whole reason the button is a form and not an `<a>`: a URL that
    delivers mail must not be reachable by anything that merely follows links."""
    notification = make_notification()

    response = admin_client.get(_resend_url(notification))

    assert response.status_code == 405
    assert notification.logs.count() == 0
    assert mail.outbox == []


def test_ac_46_view_only_staff_can_neither_see_the_button_nor_post_to_it(
    client, django_user_model, make_notification, production
):
    from django.contrib.auth.models import Permission

    viewer = django_user_model.objects.create_user(
        username="viewer", password="not-a-real-password", is_staff=True
    )
    viewer.user_permissions.add(Permission.objects.get(codename="view_notification"))
    client.force_login(viewer)
    notification = make_notification()

    changelist = client.get("/admin/notifier/notification/")
    change_page = client.get(f"/admin/notifier/notification/{notification.pk}/change/")
    posted = client.post(_resend_url(notification))

    assert "formaction" not in changelist.content.decode()
    assert "formaction" not in change_page.content.decode()
    assert posted.status_code == 403
    assert notification.logs.count() == 0
    assert mail.outbox == []


def test_template_admin_prepopulates_the_key_from_the_name(site):
    assert site._registry[NotificationTemplate].prepopulated_fields == {"key": ("name",)}

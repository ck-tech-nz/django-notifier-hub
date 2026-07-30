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


def test_template_admin_prepopulates_the_key_from_the_name(site):
    assert site._registry[NotificationTemplate].prepopulated_fields == {"key": ("name",)}

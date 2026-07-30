"""Admin registration.

Only imported by Django's admin autodiscovery, so this module never loads on a
headless install with no ``django.contrib.admin`` (PRD 2.10).
"""

from django.contrib import admin, messages
from django.contrib.admin.utils import quote, unquote
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponseNotAllowed, HttpResponseRedirect
from django.urls import path, reverse
from django.utils.html import format_html
from django.utils.http import url_has_allowed_host_and_scheme

from notifier.models import (
    DefaultRecipient,
    Notification,
    NotificationLog,
    NotificationTemplate,
)


class NotificationLogInline(admin.TabularInline):
    model = NotificationLog
    extra = 0
    can_delete = False
    show_change_link = True
    fields = ("created_at", "result", "backend", "effective_recipients", "env", "is_async", "error")
    readonly_fields = fields

    def has_add_permission(self, request, obj):
        return False


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "channel",
        "status",
        "subject",
        "source",
        "sent_at",
        "created_at",
        "resend_button",
    )
    list_filter = ("channel", "status", "created_at")
    search_fields = ("subject", "rendered_subject", "source", "recipients")
    date_hierarchy = "created_at"
    inlines = [NotificationLogInline]
    readonly_fields = (
        "rendered_subject",
        "rendered_text",
        "rendered_html",
        "send_attempts",
        "sent_at",
        "last_error",
        "created_at",
        "updated_at",
    )
    actions = ["resend_selected"]

    # -- re-sending -----------------------------------------------------
    #
    # Three ways in -- the bulk action, a button per changelist row, a button in
    # the change form's submit row -- and all three call
    # ``Notification.resend()``. There is no second send path (PRD 5.2).
    #
    # All three POST. Delivery is a side effect, so it must never hang off a URL
    # something can follow on its own: a link-prefetching browser, a crawler
    # that got the URL from a pasted screenshot, or a second click on a copied
    # address would each put mail in front of a customer.

    @admin.action(description="Re-send selected notifications")
    def resend_selected(self, request, queryset):
        for notification in queryset:
            notification.resend()
        self.message_user(
            request, f"Re-dispatched {queryset.count()} notification(s).", messages.SUCCESS
        )

    def get_urls(self):
        # Ahead of super(): ModelAdmin's trailing "<path:object_id>/" route
        # matches any suffix, "5/resend/" included.
        return [
            path(
                "<path:object_id>/resend/",
                self.admin_site.admin_view(self.resend_view),
                name=self._resend_url_name,
            ),
            *super().get_urls(),
        ]

    @property
    def _resend_url_name(self):
        return f"{self.opts.app_label}_{self.opts.model_name}_resend"

    def _resend_url(self, pk):
        return reverse(
            f"admin:{self._resend_url_name}",
            args=[quote(pk)],
            current_app=self.admin_site.name,
        )

    def resend_view(self, request, object_id):
        """Re-dispatch one notification, then return where it was asked for."""
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        notification = self.get_object(request, unquote(object_id))
        if notification is None:
            raise Http404(f"No notification with primary key {object_id!r} exists.")
        # admin_view() only proves the user is staff; the per-object check is
        # this line, so a stale button in a page loaded before a permission
        # change cannot send.
        if not self.has_change_permission(request, notification):
            raise PermissionDenied
        notification.resend()
        self.message_user(
            request, f"Re-dispatched notification {notification.pk}.", messages.SUCCESS
        )
        return HttpResponseRedirect(self._resend_redirect_to(request, notification))

    def _resend_redirect_to(self, request, notification):
        """Back to where the button was pressed, so the changelist keeps its
        filters and page, and the change form stays open on the row."""
        referer = request.META.get("HTTP_REFERER", "")
        if referer and url_has_allowed_host_and_scheme(
            referer, allowed_hosts={request.get_host()}, require_https=request.is_secure()
        ):
            return referer
        return reverse(
            f"admin:{self.opts.app_label}_{self.opts.model_name}_change",
            args=[quote(notification.pk)],
            current_app=self.admin_site.name,
        )

    def get_list_display(self, request):
        list_display = super().get_list_display(request)
        if self.has_change_permission(request):
            return list_display
        return tuple(name for name in list_display if name != "resend_button")

    @admin.display(description="")
    def resend_button(self, obj):
        """A submit button that borrows the changelist's own form -- and its CSRF
        token -- through ``formaction``. A per-row ``<form>`` is not an option:
        the changelist already is one form, and forms cannot nest."""
        return format_html(
            '<input type="submit" value="Re-send" formaction="{}">', self._resend_url(obj.pk)
        )

    def change_view(self, request, object_id, form_url="", extra_context=None):
        extra_context = {**(extra_context or {})}
        if self.has_change_permission(request):
            extra_context["notifier_resend_url"] = self._resend_url(unquote(object_id))
        return super().change_view(request, object_id, form_url, extra_context)


@admin.register(NotificationTemplate)
class NotificationTemplateAdmin(admin.ModelAdmin):
    """Editing a template is equivalent to code execution.

    Bodies are rendered with the full Django template engine, so
    ``change_notificationtemplate`` should be treated as a privileged
    permission (PRD 2.3).
    """

    list_display = ("key", "channel", "name", "is_active", "updated_at")
    list_filter = ("channel", "is_active")
    search_fields = ("key", "name", "subject")
    prepopulated_fields = {"key": ("name",)}


@admin.register(NotificationLog)
class NotificationLogAdmin(admin.ModelAdmin):
    list_display = ("id", "notification", "result", "env", "is_async", "duration_ms", "created_at")
    list_filter = ("result", "env", "is_async", "created_at")
    search_fields = ("backend", "error", "effective_recipients")
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in self.model._meta.fields]


@admin.register(DefaultRecipient)
class DefaultRecipientAdmin(admin.ModelAdmin):
    list_display = ("address", "channel", "enabled", "note", "updated_at")
    list_filter = ("channel", "enabled")
    search_fields = ("address", "note")

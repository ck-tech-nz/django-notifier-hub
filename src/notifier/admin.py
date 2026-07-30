"""Admin registration.

Only imported by Django's admin autodiscovery, so this module never loads on a
headless install with no ``django.contrib.admin`` (PRD 2.10).
"""

from django.contrib import admin, messages

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
    list_display = ("id", "channel", "status", "subject", "source", "sent_at", "created_at")
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

    @admin.action(description="Re-send selected notifications")
    def resend_selected(self, request, queryset):
        for notification in queryset:
            notification.resend()
        self.message_user(
            request, f"Re-dispatched {queryset.count()} notification(s).", messages.SUCCESS
        )


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

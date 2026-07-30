"""The four models.

PostgreSQL only: ``ArrayField``, ``GinIndex`` and array-equality check
constraints are load-bearing (PRD 2.9). No FK to ``AUTH_USER_MODEL`` anywhere,
so the app installs on a service with no users at all (PRD 2.10).
"""

from django.contrib.postgres.fields import ArrayField
from django.contrib.postgres.indexes import GinIndex
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

#: Hard ceiling on ``recipients``. A literal rather than a setting: check
#: constraints are frozen into migrations and cannot read ``settings``, so a
#: knob would drift from what the database actually enforces (PRD 2.2).
MAX_RECIPIENTS = 500


class Channel(models.TextChoices):
    EMAIL = "email", "Email"
    SMS = "sms", "SMS"
    INSITE = "insite", "In-site"


class Status(models.TextChoices):
    DRAFT = "draft", "Draft"
    READY = "ready", "Ready"
    SENDING = "sending", "Sending"
    SENT = "sent", "Sent"
    FAILED = "failed", "Failed"
    ARCHIVED = "archived", "Archived"


class LogResult(models.TextChoices):
    SENT = "sent", "Sent"
    FAILED = "failed", "Failed"
    SUPPRESSED = "suppressed", "Suppressed"
    SKIPPED = "skipped", "Skipped"


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


def _validate_recipients(value) -> list[str]:
    if not isinstance(value, list):
        raise ValidationError({"recipients": "Recipients must be a list of strings."})
    if len(value) > MAX_RECIPIENTS:
        raise ValidationError(
            {
                "recipients": f"At most {MAX_RECIPIENTS} recipients per notification; "
                f"got {len(value)}. Split the send, or raise the cap with a migration."
            }
        )
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValidationError({"recipients": "Every recipient must be a non-empty string."})
    return value


class NotificationTemplate(TimeStampedModel):
    """A named, per-channel Django template stored in the database.

    ``key`` is deliberately not unique on its own: one logical key resolves to
    one row per channel, which is what ``send_multi`` relies on (PRD 2.3).
    """

    key = models.SlugField(
        max_length=100,
        help_text="Logical lookup key, e.g. order-shipped. Shared across channels.",
    )
    name = models.CharField(max_length=128)
    channel = models.CharField(max_length=16, choices=Channel)
    subject = models.CharField(max_length=255, blank=True)
    body_text = models.TextField(blank=True)
    body_html = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("key", "channel")
        constraints = [
            models.UniqueConstraint(
                fields=["key", "channel"], name="notifier_uniq_template_key_channel"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.key} ({self.get_channel_display()})"

    def clean(self) -> None:
        from notifier.rendering import check_template_syntax

        errors: dict[str, str] = {}

        if self.channel == Channel.EMAIL:
            if not self.subject:
                errors["subject"] = "An email template needs a subject."
            if not (self.body_text or self.body_html):
                errors["body_text"] = "An email template needs a text or HTML body."
        elif self.channel == Channel.SMS:
            if not self.body_text:
                errors["body_text"] = "An SMS template needs a text body."
            if self.body_html:
                errors["body_html"] = "SMS cannot carry HTML."
        elif self.channel == Channel.INSITE and not (
            self.subject or self.body_text or self.body_html
        ):
            errors["subject"] = "An in-site template needs a subject or a body."

        # Compile now so a syntax error surfaces in the admin at edit time
        # rather than in a worker at send time (AC-36).
        for field in ("subject", "body_text", "body_html"):
            value = getattr(self, field)
            if value and field not in errors:
                error = check_template_syntax(value)
                if error:
                    errors[field] = f"Template syntax error: {error}"

        if errors:
            raise ValidationError(errors)


class Notification(TimeStampedModel):
    """The unit of work. Reaching ``status="ready"`` is what causes delivery."""

    Channel = Channel
    Status = Status

    channel = models.CharField(max_length=16, choices=Channel, db_index=True)
    status = models.CharField(max_length=16, choices=Status, default=Status.DRAFT, db_index=True)
    group_id = models.UUIDField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Shared by the sibling rows of one multi-channel send.",
    )
    source = models.CharField(
        max_length=100,
        blank=True,
        db_index=True,
        help_text='What caused this send, e.g. "order.shipped", "cron:daily-digest".',
    )

    template = models.ForeignKey(
        NotificationTemplate,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="notifications",
    )
    context = models.JSONField(default=dict, blank=True)

    subject = models.CharField(max_length=255, blank=True)
    body_text = models.TextField(blank=True)
    body_html = models.TextField(blank=True)

    recipients = ArrayField(models.CharField(max_length=255), default=list, blank=True)
    read_by = ArrayField(models.IntegerField(), default=list, blank=True)

    rendered_subject = models.CharField(max_length=255, blank=True)
    rendered_text = models.TextField(blank=True)
    rendered_html = models.TextField(blank=True)

    sent_at = models.DateTimeField(null=True, blank=True)
    send_attempts = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["status", "channel"], name="notifier_status_channel"),
            models.Index(
                fields=["channel", "status", "-created_at"], name="notifier_chan_stat_created"
            ),
            GinIndex(fields=["recipients"], name="notifier_recipients_gin"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=~Q(channel=Channel.SMS) | Q(body_html="", rendered_html=""),
                name="notifier_sms_has_no_html",
            ),
            models.CheckConstraint(
                condition=Q(channel=Channel.INSITE) | Q(read_by=[]),
                name="notifier_read_by_insite_only",
            ),
            models.CheckConstraint(
                condition=~Q(status=Status.SENT) | Q(sent_at__isnull=False),
                name="notifier_sent_requires_sent_at",
            ),
            models.CheckConstraint(
                condition=Q(recipients__len__lte=MAX_RECIPIENTS),
                name="notifier_max_recipients",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.get_channel_display()} #{self.pk} ({self.status})"

    # -- change tracking ------------------------------------------------

    @classmethod
    def from_db(cls, db, field_names, values):
        """Stash the persisted status so ``post_save`` can spot a transition.

        The cheapest correct place to capture the old value: no extra query.
        """
        instance = super().from_db(db, field_names, values)
        instance._loaded_status = instance.status
        return instance

    # -- validation -----------------------------------------------------

    def clean(self) -> None:
        errors: dict[str, str] = {}

        try:
            _validate_recipients(self.recipients)
        except ValidationError as exc:
            errors.update(exc.message_dict)

        if self.template_id and self.template.channel != self.channel:
            errors["template"] = (
                f"Template {self.template.key!r} is for the "
                f"{self.template.get_channel_display()} channel, not {self.get_channel_display()}."
            )
        if self.template_id and not self.template.is_active:
            errors["template"] = f"Template {self.template.key!r} is inactive."

        subject = self.subject or (self.template.subject if self.template_id else "")
        has_text = bool(self.body_text or (self.template.body_text if self.template_id else ""))
        has_html = bool(self.body_html or (self.template.body_html if self.template_id else ""))

        if self.channel == Channel.EMAIL:
            if not subject:
                errors["subject"] = "An email needs a subject, inline or from its template."
            if not (has_text or has_html):
                errors["body_text"] = "An email needs a text or HTML body."
        elif self.channel == Channel.SMS:
            if not has_text:
                errors["body_text"] = "An SMS needs a text body."
            if self.body_html:
                errors["body_html"] = "SMS cannot carry HTML."
        elif self.channel == Channel.INSITE and not (subject or has_text or has_html):
            errors["subject"] = "An in-site notification needs a subject or a body."

        if self.channel != Channel.INSITE and self.read_by:
            errors["read_by"] = "read_by applies to in-site notifications only."

        if errors:
            raise ValidationError(errors)

    # -- behaviour ------------------------------------------------------

    def render(self):
        """Render to a ``RenderedMessage``. Pure: no database write."""
        from notifier.rendering import render_notification

        return render_notification(self)

    def mark_ready(self, *, save: bool = True) -> None:
        """Move to ``ready``, which is what triggers delivery."""
        self.status = Status.READY
        if save:
            self.save(update_fields=["status", "updated_at"] if self.pk else None)

    def resend(self) -> None:
        """Dispatch again, bypassing the transition check."""
        from notifier.dispatch import dispatch

        dispatch(self)

    def is_read_by(self, user_id: int) -> bool:
        return int(user_id) in (self.read_by or [])

    def mark_read(self, user_id: int) -> None:
        """Add ``user_id`` to ``read_by``, once.

        Takes a row lock: this is a read-modify-write on a shared row, so two
        concurrent marks would otherwise lose one. Serialising here is
        acceptable because in-site is a secondary channel at low volume
        (PRD 6.4.1); code marking many rows at once should update the field
        directly instead of calling this in a loop.
        """
        from django.db import transaction

        user_id = int(user_id)
        with transaction.atomic():
            current = type(self).objects.select_for_update().values_list("read_by", flat=True)
            read_by = list(current.get(pk=self.pk) or [])
            if user_id in read_by:
                self.read_by = read_by
                return
            read_by.append(user_id)
            type(self).objects.filter(pk=self.pk).update(read_by=read_by)
            self.read_by = read_by


class NotificationLog(models.Model):
    """One row per delivery *attempt*. Append-only."""

    LogResult = LogResult

    notification = models.ForeignKey(Notification, on_delete=models.CASCADE, related_name="logs")
    result = models.CharField(max_length=16, choices=LogResult)
    backend = models.CharField(max_length=255, blank=True)
    requested_recipients = ArrayField(models.CharField(max_length=255), default=list, blank=True)
    effective_recipients = ArrayField(models.CharField(max_length=255), default=list, blank=True)
    env = models.CharField(max_length=32, blank=True)
    is_async = models.BooleanField(default=False)
    error = models.TextField(blank=True)
    provider_response = models.JSONField(default=dict, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    duration_ms = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["notification", "created_at"], name="notifier_log_notif_created"),
            models.Index(fields=["result", "-created_at"], name="notifier_log_result_created"),
        ]

    def __str__(self) -> str:
        return f"{self.result} for notification #{self.notification_id}"


class DefaultRecipient(TimeStampedModel):
    """The non-production safety net: who gets mail instead of the real target."""

    channel = models.CharField(max_length=16, choices=Channel)
    address = models.CharField(max_length=255)
    enabled = models.BooleanField(default=True)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ("channel", "address")
        constraints = [
            models.UniqueConstraint(
                fields=["channel", "address"], name="notifier_uniq_default_recipient"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.address} ({self.get_channel_display()})"

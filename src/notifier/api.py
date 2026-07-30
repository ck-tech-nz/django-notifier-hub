"""The public convenience API.

``send()`` and ``send_multi()`` are thin wrappers over
``Notification.objects.create()``. They add no delivery path of their own, so
anything achievable through them is achievable through the ORM.
"""

import uuid

from django.core.exceptions import ValidationError
from django.db import transaction

from notifier.models import Channel, Notification, NotificationTemplate, Status


def send(
    *,
    channel: str,
    recipients: list[str],
    key: str | None = None,
    context: dict | None = None,
    subject: str = "",
    body_text: str = "",
    body_html: str = "",
    source: str = "",
    group_id: uuid.UUID | None = None,
    status: str = Status.READY,
) -> Notification:
    """Create a notification and, at ``status="ready"``, dispatch it.

    Pass ``key`` to render from a stored template, or ``subject`` / ``body_*``
    for an inline message.
    """
    template = _resolve_template(key, channel) if key else None

    notification = Notification(
        channel=channel,
        status=status,
        template=template,
        context=context or {},
        subject=subject,
        body_text=body_text,
        body_html=body_html,
        recipients=list(recipients or []),
        source=source,
        group_id=group_id,
    )
    notification.full_clean(exclude=["read_by"])
    notification.save()
    return notification


def send_multi(
    *,
    recipients: dict[str, list[str]],
    key: str | None = None,
    context: dict | None = None,
    subject: str = "",
    body_text: str = "",
    body_html: str = "",
    source: str = "",
    require_all_templates: bool = True,
    status: str = Status.READY,
) -> uuid.UUID:
    """Send one message over several channels: one row per channel.

    All rows are created in a single transaction, so a missing template rolls
    the whole group back rather than leaving half a group behind. Delivery is
    independent after that point -- one channel failing neither blocks nor
    rolls back the others.

    Returns the shared ``group_id``. ``Notification.objects.filter(group_id=...)``
    is the entire group API.
    """
    if not recipients:
        raise ValidationError("send_multi() needs at least one channel.")

    group_id = uuid.uuid4()
    planned: list[tuple[str, list[str], NotificationTemplate | None]] = []

    for raw_channel, addresses in recipients.items():
        # Callers may pass a Channel member or a plain string; normalise so that
        # error messages read "insite", not "Channel.INSITE".
        channel = str(raw_channel)
        template = None
        if key:
            try:
                template = _resolve_template(key, channel)
            except NotificationTemplate.DoesNotExist:
                if require_all_templates:
                    raise ValidationError(
                        f"No active template {key!r} for the {channel!r} channel. "
                        f"Create one, or pass require_all_templates=False to skip the channel."
                    ) from None
                continue
        planned.append((channel, list(addresses or []), template))

    # Validate every member before saving any of them. Doing this in two passes
    # rather than validating-then-saving one at a time means an invalid channel
    # cannot leave earlier siblings created and dispatched -- a guarantee that
    # then holds regardless of whether DISPATCH_ON_COMMIT is on.
    pending = []
    for channel, addresses, template in planned:
        notification = Notification(
            channel=channel,
            status=status,
            template=template,
            context=context or {},
            subject=subject,
            body_text=body_text,
            # SMS carries no HTML, and a check constraint enforces it, so drop an
            # inline HTML body for that channel rather than failing the group.
            body_html="" if channel == Channel.SMS else body_html,
            recipients=addresses,
            source=source,
            group_id=group_id,
        )
        notification.full_clean(exclude=["read_by"])
        pending.append(notification)

    with transaction.atomic():
        for notification in pending:
            notification.save()

    return group_id


def _resolve_template(key: str, channel: str) -> NotificationTemplate:
    return NotificationTemplate.objects.get(key=key, channel=channel, is_active=True)

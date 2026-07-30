"""The one code path that sends.

``deliver()`` is the only function that hands a message to a backend. The Celery
task and the inline path both call it; the only difference is which side of the
queue it runs on, recorded as ``NotificationLog.is_async``. Do not grow a second
implementation for either path (PRD 5.2).
"""

import logging
import time
import traceback

from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.utils import timezone

from notifier import signals
from notifier.conf import notifier_settings
from notifier.models import LogResult, Notification, NotificationLog, Status
from notifier.recipients import resolve_recipients

logger = logging.getLogger("notifier")

#: Length cap on the stored traceback tail.
_ERROR_CHARS = 4000


def dispatch(notification: Notification) -> None:
    """Queue or run delivery for ``notification``.

    Wrapped in ``transaction.on_commit`` by default: without it a Celery worker
    races the enclosing transaction and gets ``DoesNotExist`` on a row that was
    just created.
    """
    if notifier_settings.DISPATCH_ON_COMMIT:
        # robust=True so that one notification's enqueue failing cannot discard
        # the pending dispatch of every other notification created in the same
        # transaction. Django runs on-commit hooks from a list it has already
        # cleared, so without this a single raising callback silently strands
        # its siblings -- which would make send_multi's promise that "one
        # channel failing neither blocks nor rolls back the others" untrue.
        transaction.on_commit(lambda: _enqueue(notification.pk), robust=True)
    else:
        _enqueue(notification.pk)


def _enqueue(notification_id: int) -> None:
    if use_celery():
        from notifier.tasks import send_notification_task

        kwargs = {}
        if notifier_settings.CELERY_QUEUE:
            kwargs["queue"] = notifier_settings.CELERY_QUEUE
        try:
            send_notification_task.apply_async(args=[notification_id], **kwargs)
        except Exception:
            # A broker outage is an operational event, not a programming error.
            # Let it strand this one notification at `ready` -- where a worker
            # will never pick it up, but `notifier_retry_failed` can -- rather
            # than propagating out of the caller's save().
            logger.exception(
                "notifier: could not enqueue notification %s; it stays at 'ready' "
                "and needs re-dispatching once the broker is reachable",
                notification_id,
            )
        return

    notification = Notification.objects.filter(pk=notification_id).first()
    if notification is None:
        logger.warning("notifier: notification %s vanished before delivery", notification_id)
        return

    try:
        deliver(notification, is_async=False)
    except ImproperlyConfigured:
        # A configuration bug must reach the developer.
        raise
    except Exception:
        # A delivery failure must not. `deliver()` re-raises retryable errors so
        # the Celery task can retry them, but inline there is nothing to retry
        # with -- and letting an SMTP error escape would throw it out of the
        # caller's `save()`, turning "the notification failed" into "creating a
        # notification crashed". The log row and the FAILED status already carry
        # the whole story.
        logger.exception("notifier: inline delivery of notification %s failed", notification_id)


def use_celery() -> bool:
    """Whether to route through Celery.

    ``USE_CELERY`` forces the answer. Left at ``None`` it is auto-detected --
    but note that "celery is importable" is not "a worker is running", so
    production should set it explicitly (PRD 5.1).
    """
    configured = notifier_settings.USE_CELERY
    if configured is not None:
        return bool(configured)

    try:
        from celery import current_app
    except ImportError:
        return False

    try:
        return bool(current_app.conf.broker_url)
    except Exception:
        return False


def deliver(notification: Notification, *, is_async: bool = False) -> NotificationLog:
    """Render, gate, send, and record exactly one attempt.

    Returns the ``NotificationLog`` row written for this attempt. Raises only if
    the backend raised a retryable exception, so the caller (the Celery task)
    can retry -- the log row is written first either way.
    """
    started_at = timezone.now()
    started_monotonic = time.monotonic()

    resolution = resolve_recipients(notification.channel, notification.recipients)
    backend = notifier_settings.load_backend(notification.channel)

    def write_log(result: str, *, error: str = "", provider_response: dict | None = None):
        finished_at = timezone.now()
        return NotificationLog.objects.create(
            notification=notification,
            result=result,
            backend=backend.dotted_path,
            requested_recipients=resolution.requested,
            effective_recipients=resolution.effective,
            env=resolution.env,
            is_async=is_async,
            error=error,
            provider_response=provider_response or {},
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=int((time.monotonic() - started_monotonic) * 1000),
        )

    # -- nothing to do --------------------------------------------------
    if not resolution.requested:
        return write_log(LogResult.SKIPPED, error="No recipients.")

    # -- non-prod, no default recipient ---------------------------------
    if resolution.suppressed:
        log = write_log(
            LogResult.SUPPRESSED,
            error=(
                f"Environment {resolution.env!r} is not production and no enabled "
                f"DefaultRecipient exists for the {notification.channel} channel."
            ),
        )
        # status stays at READY: it never claimed to have been sent, and
        # re-running it in a configured environment must just work.
        signals.notification_suppressed.send(
            sender=Notification, notification=notification, log=log
        )
        return log

    message = notification.render()
    signals.pre_send.send(
        sender=Notification,
        notification=notification,
        recipients=resolution.effective,
        message=message,
    )

    notification.status = Status.SENDING
    notification.send_attempts = (notification.send_attempts or 0) + 1
    notification.rendered_subject = message.subject
    notification.rendered_text = message.text
    notification.rendered_html = message.html
    notification.save(
        update_fields=[
            "status",
            "send_attempts",
            "rendered_subject",
            "rendered_text",
            "rendered_html",
            "updated_at",
        ]
    )

    try:
        result = backend.send(notification, resolution.effective, message)
    except Exception as exc:
        detail = _format_exception(exc)
        log = write_log(LogResult.FAILED, error=detail)
        _mark_failed(notification, detail)
        signals.notification_failed.send(
            sender=Notification, notification=notification, log=log, exception=exc
        )
        # A misconfiguration is not a delivery outcome. Swallowing it would mark
        # every notification "failed" while hiding the reason from the developer
        # who can actually fix it, so it propagates -- after being logged.
        if isinstance(exc, (ImproperlyConfigured, *backend.retryable_exceptions)):
            raise
        return log

    if not result.ok:
        detail = result.error or "The backend rejected the message."
        log = write_log(LogResult.FAILED, error=detail, provider_response=result.provider_response)
        _mark_failed(notification, detail)
        signals.notification_failed.send(
            sender=Notification, notification=notification, log=log, exception=None
        )
        return log

    log = write_log(LogResult.SENT, provider_response=result.provider_response)
    notification.status = Status.SENT
    notification.sent_at = notification.sent_at or timezone.now()
    notification.last_error = ""
    notification.save(update_fields=["status", "sent_at", "last_error", "updated_at"])
    signals.notification_sent.send(sender=Notification, notification=notification, log=log)
    return log


def _mark_failed(notification: Notification, detail: str) -> None:
    notification.status = Status.FAILED
    notification.last_error = detail
    notification.save(update_fields=["status", "last_error", "updated_at"])


def _format_exception(exc: BaseException) -> str:
    tail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    return f"{type(exc).__name__}: {exc}\n\n{tail}"[-_ERROR_CHARS:]

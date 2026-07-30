"""Celery integration, behind a guarded import.

This module imports cleanly without Celery installed -- Django is the only hard
dependency. ``pip install django-notifier-hub[celery]`` supplies the extra.
"""

import logging

logger = logging.getLogger("notifier")

try:
    from celery import shared_task

    CELERY_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised by the no-celery environment
    CELERY_AVAILABLE = False

    def _unavailable(*args, **kwargs):
        raise RuntimeError(
            "Celery is not installed. Install django-notifier-hub[celery], or set "
            "NOTIFIER['USE_CELERY'] = False to deliver inline."
        )

    def shared_task(*args, **kwargs):
        def decorator(func):
            func.delay = _unavailable
            func.apply_async = _unavailable
            return func

        return decorator(args[0]) if args and callable(args[0]) else decorator


def retry_countdown(attempts: int, backoff: int) -> int:
    """Seconds to wait before attempt ``attempts + 1``.

    Exponential from the configured base: 30, 60, 120 for the default 30.
    """
    return backoff * (2 ** (attempts - 1))


def handle_failed_attempt(notification, exc, *, attempts: int, max_retries: int) -> int | None:
    """Decide what happens after a failed attempt.

    Returns the countdown for another attempt, or ``None`` when the retry budget
    is spent -- in which case ``notification_exhausted`` has been sent.

    Split out from the task body so the arithmetic and the give-up signal can be
    asserted directly, without depending on a broker or on how a Celery version
    happens to schedule retries.
    """
    from notifier.conf import notifier_settings
    from notifier.models import Notification
    from notifier.signals import notification_exhausted

    if attempts > max_retries:
        # "attempt N of N failed" and "we have stopped trying" are different
        # events, and only the second is worth alerting on.
        notification.refresh_from_db()
        notification_exhausted.send(
            sender=Notification,
            notification=notification,
            log=notification.logs.first(),
            attempts=attempts,
        )
        logger.warning(
            "notifier: giving up on notification %s after %s attempt(s): %s",
            notification.pk,
            attempts,
            exc,
        )
        return None

    return retry_countdown(attempts, notifier_settings.CELERY_RETRY_BACKOFF)


@shared_task(bind=True, max_retries=None)
def send_notification_task(self, notification_id: int):
    """Deliver one notification, retrying the backend's retryable exceptions.

    Each attempt writes its own ``NotificationLog`` row, so the log is a full
    history rather than a last-known state.
    """
    from notifier.conf import notifier_settings
    from notifier.dispatch import deliver
    from notifier.models import Notification

    notification = Notification.objects.filter(pk=notification_id).first()
    if notification is None:
        logger.warning("notifier: notification %s vanished before delivery", notification_id)
        return None

    max_retries = notifier_settings.CELERY_MAX_RETRIES
    try:
        log = deliver(notification, is_async=True)
    except Exception as exc:
        attempts = (self.request.retries or 0) + 1
        countdown = handle_failed_attempt(
            notification, exc, attempts=attempts, max_retries=max_retries
        )
        if countdown is None:
            return None
        raise self.retry(exc=exc, countdown=countdown, max_retries=max_retries) from exc

    return log.pk if log else None

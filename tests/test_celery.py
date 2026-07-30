"""AC-14…AC-16, AC-36: the async delivery path.

These exercise the same ``dispatch.deliver()`` the inline path uses -- the only
differences are which side of the queue it runs on and ``log.is_async``.
"""

import pytest

from notifier.dispatch import use_celery
from notifier.models import LogResult, Status
from notifier.signals import notification_exhausted, notification_failed

celery = pytest.importorskip("celery")

pytestmark = pytest.mark.django_db


@pytest.fixture
def celery_app():
    """An eager Celery app, so tasks execute in-process."""
    app = celery.Celery("notifier-tests", broker="memory://", backend="cache+memory://")
    app.conf.task_always_eager = True
    app.conf.task_eager_propagates = False
    app.set_current()
    yield app
    app.close()


@pytest.fixture
def async_delivery(settings, celery_app):
    settings.NOTIFIER = {**settings.NOTIFIER, "USE_CELERY": True}
    return celery_app


def test_ac_14_inline_delivery_is_not_flagged_async(make_notification, production, settings):
    settings.NOTIFIER = {**settings.NOTIFIER, "USE_CELERY": False}
    notification = make_notification(status=Status.DRAFT)

    notification.mark_ready()

    log = notification.logs.get()
    assert log.result == LogResult.SENT
    assert log.is_async is False


def test_ac_15_async_delivery_is_flagged_async(make_notification, production, async_delivery):
    notification = make_notification(status=Status.DRAFT)

    notification.mark_ready()

    log = notification.logs.get()
    assert log.result == LogResult.SENT
    assert log.is_async is True
    notification.refresh_from_db()
    assert notification.status == Status.SENT


def test_use_celery_setting_forces_the_answer(settings, celery_app):
    settings.NOTIFIER = {**settings.NOTIFIER, "USE_CELERY": False}
    assert use_celery() is False

    settings.NOTIFIER = {**settings.NOTIFIER, "USE_CELERY": True}
    assert use_celery() is True


def test_use_celery_autodetects_a_configured_broker(settings, celery_app):
    settings.NOTIFIER = {**settings.NOTIFIER, "USE_CELERY": None}

    # The fixture's app has a broker, so detection says yes.
    assert use_celery() is True

    celery_app.conf.broker_url = None
    assert use_celery() is False


@pytest.fixture
def failing_sms(settings):
    settings.NOTIFIER = {
        **settings.NOTIFIER,
        "CELERY_MAX_RETRIES": 2,
        "CELERY_RETRY_BACKOFF": 30,
        "BACKENDS": {
            **settings.NOTIFIER["BACKENDS"],
            "sms": "notifier.backends.sms.FailingSmsBackend",
        },
    }
    return settings


@pytest.fixture
def collect():
    """Collect a signal's payloads, disconnecting afterwards."""
    connected = []

    def _collect(signal):
        received = []
        receiver = lambda **kwargs: received.append(kwargs)  # noqa: E731
        signal.connect(receiver, weak=False)
        connected.append((signal, receiver))
        return received

    yield _collect
    for signal, receiver in connected:
        signal.disconnect(receiver)


def test_ac_16_every_attempt_writes_its_own_log(
    make_notification, production, failing_sms, async_delivery, collect
):
    """A retry is another `deliver()`, and each attempt records itself.

    End to end through Celery: eager mode does re-execute retries, so the whole
    first-try-plus-two-retries sequence runs in-process.
    """
    failures = collect(notification_failed)
    notification = make_notification(
        status=Status.DRAFT,
        channel="sms",
        recipients=["+6421234567"],
        subject="",
        body_text="Text.",
    )

    notification.mark_ready()

    notification.refresh_from_db()
    assert notification.status == Status.FAILED
    assert notification.last_error
    # CELERY_MAX_RETRIES=2, so three attempts and three log rows -- a full
    # history rather than a last-known state.
    assert notification.send_attempts == 3
    assert notification.logs.count() == 3
    assert {log.result for log in notification.logs.all()} == {LogResult.FAILED}
    assert {log.is_async for log in notification.logs.all()} == {True}
    assert len(failures) == 3


def test_ac_16_retry_backoff_is_exponential_from_the_configured_base():
    from notifier.tasks import retry_countdown

    assert [retry_countdown(n, 30) for n in (1, 2, 3)] == [30, 60, 120]
    assert [retry_countdown(n, 5) for n in (1, 2, 3)] == [5, 10, 20]


def test_ac_36_a_failure_within_budget_asks_for_another_attempt(
    make_notification, production, failing_sms, collect
):
    from notifier.tasks import handle_failed_attempt

    exhausted = collect(notification_exhausted)
    notification = make_notification(status=Status.DRAFT, channel="sms", recipients=["+64211"])

    countdown = handle_failed_attempt(notification, RuntimeError("nope"), attempts=1, max_retries=2)

    assert countdown == 30
    assert exhausted == []


def test_ac_36_exhausting_the_budget_fires_exhausted_exactly_once(
    make_notification, production, failing_sms, collect
):
    from notifier.dispatch import deliver
    from notifier.tasks import handle_failed_attempt

    exhausted = collect(notification_exhausted)
    notification = make_notification(
        status=Status.DRAFT,
        channel="sms",
        recipients=["+6421234567"],
        subject="",
        body_text="Text.",
    )
    with pytest.raises(RuntimeError):
        deliver(notification, is_async=True)

    # attempts (3) exceeds max_retries (2): the budget is spent.
    countdown = handle_failed_attempt(notification, RuntimeError("nope"), attempts=3, max_retries=2)

    assert countdown is None
    assert len(exhausted) == 1
    assert exhausted[0]["attempts"] == 3
    assert exhausted[0]["notification"].pk == notification.pk
    # It reports the log of the final attempt rather than writing a new row.
    assert exhausted[0]["log"].pk == notification.logs.first().pk


def test_the_task_asks_celery_to_retry_rather_than_swallowing(
    make_notification, production, failing_sms, async_delivery
):
    """A retryable failure surfaces as `Retry`, with the configured backoff."""
    from celery.exceptions import Retry

    from notifier.tasks import send_notification_task

    notification = make_notification(
        status=Status.DRAFT,
        channel="sms",
        recipients=["+6421234567"],
        subject="",
        body_text="Text.",
    )

    send_notification_task.push_request(
        args=[notification.pk], kwargs={}, retries=0, called_directly=False
    )
    try:
        with pytest.raises(Retry) as exc:
            send_notification_task(notification.pk)
    finally:
        send_notification_task.pop_request()

    # First retry waits the base backoff, and every attempt is on the record.
    assert exc.value.when == 30
    assert notification.logs.count() == 3


def test_the_task_gives_up_once_the_budget_is_spent(
    make_notification, production, failing_sms, async_delivery, collect
):
    from notifier.tasks import send_notification_task

    exhausted = collect(notification_exhausted)
    notification = make_notification(
        status=Status.DRAFT,
        channel="sms",
        recipients=["+6421234567"],
        subject="",
        body_text="Text.",
    )

    # retries=2 makes this the third attempt, one past CELERY_MAX_RETRIES=2.
    send_notification_task.push_request(
        args=[notification.pk], kwargs={}, retries=2, called_directly=False
    )
    try:
        assert send_notification_task(notification.pk) is None
    finally:
        send_notification_task.pop_request()

    assert len(exhausted) == 1
    assert exhausted[0]["attempts"] == 3


def test_a_non_retryable_failure_is_not_retried(
    make_notification, production, settings, async_delivery
):
    settings.NOTIFIER = {
        **settings.NOTIFIER,
        "USE_CELERY": True,
        "CELERY_MAX_RETRIES": 3,
        "BACKENDS": {
            **settings.NOTIFIER["BACKENDS"],
            "sms": "tests.test_celery.RejectingSmsBackend",
        },
    }
    notification = make_notification(
        status=Status.DRAFT,
        channel="sms",
        recipients=["+6421234567"],
        subject="",
        body_text="Text.",
    )

    notification.mark_ready()

    notification.refresh_from_db()
    assert notification.status == Status.FAILED
    # ok=False is a provider rejection: retrying will not help, so exactly one.
    assert notification.logs.count() == 1


def test_task_tolerates_a_deleted_notification(async_delivery):
    from notifier.tasks import send_notification_task

    assert send_notification_task.apply(args=[999_999]).get() is None


class RejectingSmsBackend:
    """Reports a permanent provider rejection rather than raising."""

    retryable_exceptions = ()
    options: dict = {}

    @property
    def dotted_path(self):
        return "tests.test_celery.RejectingSmsBackend"

    def send(self, notification, recipients, message):
        from notifier.backends.base import BackendResult

        return BackendResult.failure("Number blocked by the carrier.")

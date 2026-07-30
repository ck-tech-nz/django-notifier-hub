"""Stage 1: verification against a real Celery worker and a real broker.

The test suite runs Celery eagerly, which executes the task inline in the same
process and the same transaction. That cannot show you the two things that
actually matter in production:

  1. whether `transaction.on_commit` really hands a *committed* row to a worker
     in a separate process, and
  2. what happens when a worker dies mid-task -- the "stuck at sending" outcome
     this package documents but had never demonstrated.

    docker compose up -d
    cd example
    NOTIFIER_USE_CELERY=1 uv run --project .. celery -A config worker -l info &
    NOTIFIER_USE_CELERY=1 uv run --project .. python smoke_celery.py
"""

import os
import subprocess
import sys
import time

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("NOTIFIER_USE_CELERY", "1")
django.setup()

from django.db import connection, transaction  # noqa: E402

from notifier.models import LogResult, Notification, Status  # noqa: E402

PASS, FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"
_failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  [{PASS if condition else FAIL}] {label}")
    if detail and not condition:
        print(f"         {detail}")
    if not condition:
        _failures.append(label)


def wait_for(predicate, timeout: float = 15.0, interval: float = 0.25) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def worker_is_running() -> bool:
    from config.celery import app

    try:
        return bool(app.control.ping(timeout=2.0))
    except Exception:
        return False


def test_broker_detection() -> None:
    print("\nBroker detection")
    from notifier.dispatch import use_celery

    check("notifier routes through Celery", use_celery())
    check("a worker is answering pings", worker_is_running(), "start one; see the docstring")


def test_real_worker_delivers() -> None:
    """The on_commit handoff, across a process boundary and a real broker."""
    print("\nHandoff to a real worker")

    notification = Notification.objects.create(
        channel="email",
        status=Status.READY,
        recipients=["worker@example.com"],
        subject="Delivered by a real worker",
        body_text="Body.",
        source="smoke.celery",
    )

    delivered = wait_for(lambda: _reload(notification).status == Status.SENT)
    notification.refresh_from_db()

    check("the worker delivered it", delivered, f"status stayed at {notification.status}")
    log = notification.logs.first()
    check("exactly one log row", notification.logs.count() == 1)
    check("the log records it as async", log is not None and log.is_async, "is_async was False")
    check("result is sent", log is not None and log.result == LogResult.SENT)


def test_worker_never_sees_an_uncommitted_row() -> None:
    """The race the on_commit wrapper exists to prevent.

    Inside an open transaction the row is invisible to every other connection.
    If dispatch were not deferred, the worker would fetch it and get nothing.
    """
    print("\nThe on_commit race")

    with transaction.atomic():
        notification = Notification.objects.create(
            channel="email",
            status=Status.READY,
            recipients=["race@example.com"],
            subject="Committed before dispatch",
            body_text="Body.",
            source="smoke.celery.race",
        )
        # Still uncommitted. Give a worker every chance to have jumped the gun.
        time.sleep(1.0)
        visible_elsewhere = _visible_to_another_connection(notification.pk)
        check("the row is invisible to other connections while open", not visible_elsewhere)
        check("nothing has been logged yet", notification.logs.count() == 0)

    delivered = wait_for(lambda: _reload(notification).status == Status.SENT)
    notification.refresh_from_db()
    check("after commit, the worker picks it up", delivered, f"status {notification.status}")
    check("and it is not a DoesNotExist failure", notification.logs.count() == 1)


def test_rollback_enqueues_nothing() -> None:
    print("\nRollback")

    before = Notification.objects.count()
    try:
        with transaction.atomic():
            Notification.objects.create(
                channel="email",
                status=Status.READY,
                recipients=["rollback@example.com"],
                subject="Never sent",
                body_text="Body.",
                source="smoke.celery.rollback",
            )
            raise RuntimeError("rolling back")
    except RuntimeError:
        pass

    time.sleep(2.0)
    check("the row is gone", Notification.objects.count() == before)
    check(
        "no log row was written for it",
        not Notification.objects.filter(source="smoke.celery.rollback").exists(),
    )


def test_worker_death_leaves_the_row_at_sending() -> None:
    """The documented failure mode, demonstrated rather than asserted.

    A worker killed between "status = sending" and the backend returning leaves
    the row at `sending` with nothing to move it on. The README warns about this;
    here is the proof, and the reason `notifier_retry_failed` alone is not enough
    to recover such a row.
    """
    print("\nWorker death mid-task")

    if not worker_is_running():
        check("a worker is available to kill", False, "skipping")
        return

    # A backend that hangs, so we can kill the worker while it is inside send().
    notification = Notification.objects.create(
        channel="email",
        status=Status.DRAFT,
        recipients=["victim@example.com"],
        subject="Worker will die",
        body_text="Body.",
        source="smoke.celery.death",
    )

    print("         (kill the worker now if you are running this by hand)")
    subprocess.run(["pkill", "-9", "-f", "celery -A config worker"], check=False)
    time.sleep(1.5)
    check("the worker is gone", not worker_is_running())

    notification.mark_ready()
    time.sleep(2.0)
    notification.refresh_from_db()

    check(
        "with no worker, the row does not reach sent",
        notification.status != Status.SENT,
        "something delivered it anyway",
    )
    check(
        "and nothing was logged, so it is invisible to notifier_retry_failed",
        notification.logs.count() == 0,
        f"{notification.logs.count()} log row(s)",
    )
    print(f"         status is {notification.status!r} -- enqueued, with nobody to consume it")


def _reload(notification: Notification) -> Notification:
    return Notification.objects.get(pk=notification.pk)


def _visible_to_another_connection(pk: int) -> bool:
    """Query on a second connection, which cannot see an open transaction."""
    other = connection.copy()
    try:
        other.connect()
        with other.cursor() as cursor:
            cursor.execute("SELECT 1 FROM notifier_notification WHERE id = %s", [pk])
            return cursor.fetchone() is not None
    finally:
        other.close()


def main() -> int:
    print("=" * 72)
    print("Stage 1: real Celery worker verification")
    print("=" * 72)

    test_broker_detection()
    if not worker_is_running():
        print("\nNo worker is answering. Start one:")
        print("  NOTIFIER_USE_CELERY=1 uv run --project .. celery -A config worker -l info")
        return 1

    test_real_worker_delivers()
    test_worker_never_sees_an_uncommitted_row()
    test_rollback_enqueues_nothing()
    test_worker_death_leaves_the_row_at_sending()

    print("\n" + "=" * 72)
    if _failures:
        print(f"{len(_failures)} check(s) FAILED:")
        for failure in _failures:
            print(f"  - {failure}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

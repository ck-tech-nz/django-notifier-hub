"""Demonstrate the two distinct ways a notification can be stranded.

They are different, and the documentation conflated them:

  A. enqueued but never consumed (no worker)  -> stays at `ready`
  B. worker died inside the backend send()    -> stays at `sending`

Neither writes a log row, so neither is visible to `notifier_retry_failed`,
which selects on status=failed.

    docker compose up -d
    cd example && uv run --project .. python smoke_celery_stuck.py
"""

import os
import signal
import subprocess
import sys
import time

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("NOTIFIER_USE_CELERY", "1")
django.setup()

from notifier.models import Notification, Status  # noqa: E402

PASS, FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"
_failures = []


def check(label, condition, detail=""):
    print(f"  [{PASS if condition else FAIL}] {label}")
    if detail and not condition:
        print(f"         {detail}")
    if not condition:
        _failures.append(label)


def wait_for(predicate, timeout=20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.25)
    return False


def status_of(pk):
    return Notification.objects.get(pk=pk).status


print("=" * 72)
print("Stranded rows: the two failure modes, distinguished")
print("=" * 72)

subprocess.run(["pkill", "-9", "-f", "celery -A config worker"], check=False)
time.sleep(1.0)

# -- A: no worker at all ----------------------------------------------------
print("\nA. Enqueued with no worker running")
a = Notification.objects.create(
    channel="email",
    status=Status.READY,
    recipients=["a@example.com"],
    subject="No worker",
    body_text="Body.",
    source="stuck.no_worker",
)
time.sleep(2.0)
a.refresh_from_db()
check("status stays at ready, NOT sending", a.status == Status.READY, f"got {a.status!r}")
check("no log row exists", a.logs.count() == 0)

# -- B: worker killed inside send() -----------------------------------------
print("\nB. Worker killed while inside the backend's send()")
env = {
    **os.environ,
    "NOTIFIER_USE_CELERY": "1",
    "NOTIFIER_EMAIL_BACKEND_OVERRIDE": "config.slow_backend.HangingEmailBackend",
}
worker = subprocess.Popen(
    [
        "uv",
        "run",
        "--project",
        "..",
        "celery",
        "-A",
        "config",
        "worker",
        "-l",
        "warning",
        "--concurrency",
        "1",
    ],
    env=env,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    start_new_session=True,
)
time.sleep(14.0)

b = Notification.objects.create(
    channel="email",
    status=Status.READY,
    recipients=["b@example.com"],
    subject="Worker dies mid-send",
    body_text="Body.",
    source="stuck.mid_send",
)

entered = wait_for(lambda: status_of(b.pk) == Status.SENDING)
check("the worker reached status=sending", entered, f"status was {status_of(b.pk)!r}")

if entered:
    os.killpg(os.getpgid(worker.pid), signal.SIGKILL)
    time.sleep(2.0)
    b.refresh_from_db()
    check("the row is stranded at sending", b.status == Status.SENDING, f"got {b.status!r}")
    check("no log row was written", b.logs.count() == 0)
    check("send_attempts was incremented", b.send_attempts == 1, f"got {b.send_attempts}")
else:
    subprocess.run(["pkill", "-9", "-f", "celery -A config worker"], check=False)

# -- what recovery sees -----------------------------------------------------
print("\nWhat notifier_retry_failed can see")
failed = Notification.objects.filter(status=Status.FAILED).count()
check("neither stranded row is status=failed", failed == 0, f"{failed} failed row(s)")
print(
    f"         A is {status_of(a.pk)!r}, B is {status_of(b.pk)!r} -- retry_failed matches neither"
)

subprocess.run(["pkill", "-9", "-f", "celery -A config worker"], check=False)

print("\n" + "=" * 72)
if _failures:
    print(f"{len(_failures)} check(s) FAILED:")
    for f in _failures:
        print(f"  - {f}")
    sys.exit(1)
print("All checks passed.")

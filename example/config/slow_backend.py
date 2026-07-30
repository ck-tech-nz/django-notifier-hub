"""A backend that blocks inside send(), so a worker can be killed mid-delivery.

Used only by smoke_celery_stuck.py to demonstrate the one path that really does
strand a row at `sending`.
"""

import time

from notifier.backends.base import BackendResult, BaseBackend


class HangingEmailBackend(BaseBackend):
    def send(self, notification, recipients, message):
        time.sleep(60)
        return BackendResult.success()

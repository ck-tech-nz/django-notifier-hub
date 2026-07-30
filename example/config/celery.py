"""Celery app for the example project.

`notifier.tasks.send_notification_task` is a `shared_task`, so it registers
itself once this app exists and autodiscovery has run.
"""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("example")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

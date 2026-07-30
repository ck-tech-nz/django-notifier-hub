"""Make the Celery app load with Django, the canonical arrangement.

Without this, `shared_task` has no app to bind to and `notifier`'s auto-detection
would find no configured broker.
"""

from config.celery import app as celery_app

__all__ = ["celery_app"]

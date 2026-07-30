"""A real Django project, not a test harness.

Deliberately configured the way a host project would be, so that anything the
test suite cannot see -- app loading order, a real SMTP conversation, a real
broker -- has somewhere to go wrong.

Everything points at `docker compose up -d`. Nothing here is secret: the stack
is local-only and listens on loopback.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = "example-project-not-a-real-secret"
DEBUG = True
ALLOWED_HOSTS = ["localhost", "127.0.0.1"]

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.messages",
    "django.contrib.sessions",
    "django.contrib.staticfiles",
    "django.contrib.admin",
    "django.contrib.postgres",
    "notifier",
]

MIDDLEWARE = [
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

ROOT_URLCONF = "config.urls"
STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
TIME_ZONE = "Pacific/Auckland"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("PGDATABASE", "notifier_example"),
        "USER": os.environ.get("PGUSER", "notifier"),
        "PASSWORD": os.environ.get("PGPASSWORD", "notifier"),
        "HOST": os.environ.get("PGHOST", "127.0.0.1"),
        "PORT": os.environ.get("PGPORT", "45432"),
    }
}

# -- A real SMTP server, not locmem -----------------------------------------
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = os.environ.get("EMAIL_HOST", "127.0.0.1")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "1025"))
EMAIL_USE_TLS = False
DEFAULT_FROM_EMAIL = "notifications@example.com"

# -- A real broker ----------------------------------------------------------
CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://127.0.0.1:46379/0")
CELERY_RESULT_BACKEND = CELERY_BROKER_URL
CELERY_TASK_ALWAYS_EAGER = False

# `production` here means "do not redirect recipients"; it is what makes this
# stack able to exercise the real path. MailPit is the only thing downstream, so
# no message can escape to a person.
DJANGO_ENV = os.environ.get("DJANGO_ENV", "production")

NOTIFIER = {
    "USE_CELERY": os.environ.get("NOTIFIER_USE_CELERY", "0") == "1",
    "TEMPLATE_CONTEXT_PROCESSOR": "config.notifier_context.extra_context",
}

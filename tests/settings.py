"""Test settings: the full configuration, with auth and admin installed.

See ``settings_minimal.py`` for the headless counterpart (AC-35).

Database parameters come from the environment so no connection detail is ever
committed. Defaults target a local server; override with NOTIFIER_TEST_DB_*.
"""

import os

SECRET_KEY = "notifier-tests-not-a-real-secret"
DEBUG = False
USE_TZ = True
TIME_ZONE = "UTC"

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.messages",
    "django.contrib.admin",
    "django.contrib.sessions",
    "django.contrib.staticfiles",
    # Required by ArrayField: Django enforces it via the postgres.E005
    # system check. Carries no models and needs neither auth nor contenttypes.
    "django.contrib.postgres",
    "notifier",
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("NOTIFIER_TEST_DB_NAME", "notifier_test"),
        "USER": os.environ.get("NOTIFIER_TEST_DB_USER", "postgres"),
        "PASSWORD": os.environ.get("NOTIFIER_TEST_DB_PASSWORD", "postgres"),
        "HOST": os.environ.get("NOTIFIER_TEST_DB_HOST", "127.0.0.1"),
        "PORT": os.environ.get("NOTIFIER_TEST_DB_PORT", "5432"),
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# locmem, so nothing can leave the process during a test run.
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
DEFAULT_FROM_EMAIL = "notifier@example.com"

ROOT_URLCONF = "tests.urls"
STATIC_URL = "/static/"

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

MIDDLEWARE = [
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

# Tests assert production behaviour explicitly where they need it; the default
# here is non-production so that a test forgetting to set it cannot "send".
DJANGO_ENV = "test"

NOTIFIER = {
    "PRODUCTION_ENVS": ("prod", "production"),
    "USE_CELERY": False,
    # pytest-django wraps each test in a transaction it rolls back, so
    # transaction.on_commit callbacks never fire. Dispatching inline keeps the
    # suite readable; everything after the on_commit wrapper is the same code
    # either way. The wrapper itself is what AC-05 tests, and those tests turn
    # this back on and drive the callbacks explicitly.
    "DISPATCH_ON_COMMIT": False,
    "BACKENDS": {
        "email": "notifier.backends.email.DjangoEmailBackend",
        "sms": "notifier.backends.sms.LocmemSmsBackend",
        "insite": "notifier.backends.insite.InsiteBackend",
    },
}

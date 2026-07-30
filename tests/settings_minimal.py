"""Headless test settings: the shortest configuration that works.

This proves the claim in PRD 2.10 -- no ``django.contrib.auth``, no ``admin``,
no ``contenttypes``, and therefore no user model at all. If any module under
``notifier`` grows a module-scope import of those apps, the suite run against
this module fails (AC-35).

Two requirements are real and were missed in the first draft of the PRD:

- ``django.contrib.postgres`` must be installed, despite the ``ArrayField``
  documentation not saying so: Django enforces it with the ``postgres.E005``
  system check.
- ``TEMPLATES`` must contain a ``DjangoTemplates`` backend, because bodies render
  through the project's configured engine.

Neither weakens the headless property -- ``django.contrib.postgres`` has no
models and needs neither auth nor contenttypes, and a template engine needs no
apps at all.
"""

from tests.settings import DATABASES, DEFAULT_AUTO_FIELD, SECRET_KEY  # noqa: F401

DEBUG = False
USE_TZ = True
TIME_ZONE = "UTC"

INSTALLED_APPS = ["django.contrib.postgres", "notifier"]

# No context processors: there is no request at send time.
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": False,
        "OPTIONS": {},
    }
]

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
DEFAULT_FROM_EMAIL = "notifier@example.com"

DJANGO_ENV = "test"

NOTIFIER = {
    "PRODUCTION_ENVS": ("prod", "production"),
    "USE_CELERY": False,
    # See tests/settings.py for why the on-commit wrapper is off in tests.
    "DISPATCH_ON_COMMIT": False,
    "BACKENDS": {
        "email": "notifier.backends.email.DjangoEmailBackend",
        "sms": "notifier.backends.sms.LocmemSmsBackend",
        "insite": "notifier.backends.insite.InsiteBackend",
    },
}

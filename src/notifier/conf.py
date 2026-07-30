"""Settings access for the notifier app.

One dict, ``NOTIFIER``, shallow-merged over the defaults below. Nested dicts
(``EMAIL``, ``SMS``, ``BACKENDS``) are merged one level deep, so a project can
override a single key without restating the whole sub-dict.

Read through the module-level ``notifier_settings`` singleton, never through
``django.conf.settings`` directly -- the singleton caches, and it invalidates
itself on ``setting_changed`` so ``override_settings`` works in tests.
"""

import os
from typing import Any

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.signals import setting_changed
from django.dispatch import receiver
from django.utils.module_loading import import_string

SETTING_NAME = "NOTIFIER"

DEFAULTS: dict[str, Any] = {
    # ---- environment gating -------------------------------------------
    "ENV": None,
    "PRODUCTION_ENVS": ("prod", "production"),
    "NON_PROD_SUBJECT_PREFIX": "[{env}] ",
    "SUPPRESS_WHEN_NO_DEFAULT_RECIPIENT": True,
    # ---- backends -----------------------------------------------------
    "BACKENDS": {
        "email": "notifier.backends.email.DjangoEmailBackend",
        "sms": "notifier.backends.sms.ConsoleSmsBackend",
        "insite": "notifier.backends.insite.InsiteBackend",
    },
    # ---- email --------------------------------------------------------
    "EMAIL": {
        "FROM": None,
        "CONNECTION": None,
        "FAIL_SILENTLY": False,
        # "auto" is "to" for a single recipient and "bcc" for several. With one
        # recipient there is nobody to leak an address to, so the message may as
        # well look normal; past one, hiding the list is the recoverable default
        # (PRD 6.2).
        "RECIPIENT_MODE": "auto",
    },
    # ---- sms ----------------------------------------------------------
    "SMS": {
        "FROM": None,
        "OPTIONS": {},
    },
    # ---- delivery -----------------------------------------------------
    "USE_CELERY": None,
    "CELERY_QUEUE": None,
    "CELERY_MAX_RETRIES": 3,
    "CELERY_RETRY_BACKOFF": 30,
    "DISPATCH_ON_COMMIT": True,
    # ---- rendering ----------------------------------------------------
    "TEMPLATE_CONTEXT_PROCESSOR": None,
    # ---- housekeeping -------------------------------------------------
    "LOG_RETENTION_DAYS": 90,
}

#: Sub-dicts merged one level deep rather than replaced wholesale.
_NESTED = ("BACKENDS", "EMAIL", "SMS")

#: Valid values for EMAIL["RECIPIENT_MODE"].
RECIPIENT_MODES = ("auto", "bcc", "to", "separate")

_ENV_FALLBACK = "dev"


class NotifierSettings:
    """Lazily-resolved, cached view over ``settings.NOTIFIER``."""

    def __init__(self) -> None:
        self._cache: dict[str, Any] = {}

    # -- resolution -----------------------------------------------------

    @property
    def _user(self) -> dict[str, Any]:
        return getattr(settings, SETTING_NAME, None) or {}

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        if name not in DEFAULTS:
            raise AttributeError(f"{SETTING_NAME} has no setting {name!r}")
        if name not in self._cache:
            self._cache[name] = self._resolve(name)
        return self._cache[name]

    def _resolve(self, name: str) -> Any:
        default = DEFAULTS[name]
        if name not in self._user:
            return default
        value = self._user[name]
        if name in _NESTED:
            if not isinstance(value, dict):
                raise ImproperlyConfigured(
                    f"{SETTING_NAME}[{name!r}] must be a dict, got {type(value).__name__}."
                )
            return {**default, **value}
        return value

    def reset(self) -> None:
        self._cache.clear()

    # -- derived values -------------------------------------------------

    @property
    def env(self) -> str:
        """Resolved environment name.

        ``NOTIFIER["ENV"]`` -> ``settings.DJANGO_ENV`` -> ``$DJANGO_ENV`` ->
        ``"dev"``. Django settings outrank the environment variable because
        most projects keep configuration in the settings module; the env var
        stays available for twelve-factor deployments that inject it.

        The final fallback is load-bearing: a project that configures nothing
        is treated as non-production and so cannot reach real recipients.
        """
        return (
            self.ENV
            or getattr(settings, "DJANGO_ENV", None)
            or os.environ.get("DJANGO_ENV")
            or _ENV_FALLBACK
        )

    @property
    def is_production(self) -> bool:
        return self.env in self.PRODUCTION_ENVS

    @property
    def recipient_mode(self) -> str:
        mode = self.EMAIL["RECIPIENT_MODE"]
        if mode not in RECIPIENT_MODES:
            raise ImproperlyConfigured(
                f"{SETTING_NAME}['EMAIL']['RECIPIENT_MODE'] must be one of "
                f"{', '.join(RECIPIENT_MODES)}; got {mode!r}."
            )
        return mode

    def backend_path(self, channel: str) -> str:
        channel = str(channel)
        try:
            return self.BACKENDS[channel]
        except KeyError:
            raise ImproperlyConfigured(
                f"No backend configured for channel {channel!r}. "
                f"Set {SETTING_NAME}['BACKENDS'][{channel!r}]."
            ) from None

    def load_backend(self, channel: str):
        """Instantiate the backend for ``channel``."""
        path = self.backend_path(channel)
        try:
            klass = import_string(path)
        except ImportError as exc:
            raise ImproperlyConfigured(
                f"Could not import backend {path!r} for channel {channel!r}: {exc}"
            ) from exc
        return klass()

    def template_context(self) -> dict[str, Any]:
        """Extra context merged *under* ``Notification.context``."""
        path = self.TEMPLATE_CONTEXT_PROCESSOR
        if not path:
            return {}
        try:
            func = import_string(path)
        except ImportError as exc:
            raise ImproperlyConfigured(
                f"Could not import {SETTING_NAME}['TEMPLATE_CONTEXT_PROCESSOR'] {path!r}: {exc}"
            ) from exc
        extra = func()
        if not isinstance(extra, dict):
            raise ImproperlyConfigured(
                f"{SETTING_NAME}['TEMPLATE_CONTEXT_PROCESSOR'] {path!r} must return a dict, "
                f"got {type(extra).__name__}."
            )
        return extra


notifier_settings = NotifierSettings()


@receiver(setting_changed)
def _reset_notifier_settings(sender, setting, **kwargs):
    if setting in {SETTING_NAME, "DJANGO_ENV"}:
        notifier_settings.reset()

"""System checks.

Django already rejects a missing ``django.contrib.postgres`` with
``postgres.E005``, once per ``ArrayField`` -- four near-identical errors that do
not say what to do. This adds one actionable message instead.
"""

from django.core.checks import Error, Tags, register


@register()
def check_installed_apps(app_configs, **kwargs):
    from django.apps import apps

    errors = []
    if not apps.is_installed("django.contrib.postgres"):
        errors.append(
            Error(
                "django.contrib.postgres must be in INSTALLED_APPS.",
                hint=(
                    'Add "django.contrib.postgres" to INSTALLED_APPS. notifier uses ArrayField, '
                    "which Django only permits when that app is installed. It has no models and "
                    "does not require auth or contenttypes, so a headless install is still just "
                    '["django.contrib.postgres", "notifier"].'
                ),
                id="notifier.E001",
            )
        )
    return errors


@register()
def check_template_engine(app_configs, **kwargs):
    from django.template import engines
    from django.template.backends.django import DjangoTemplates

    if any(isinstance(engine, DjangoTemplates) for engine in engines.all()):
        return []
    return [
        Error(
            "No DjangoTemplates backend is configured in TEMPLATES.",
            hint=(
                "notifier renders notification bodies with the Django template engine, so "
                "TEMPLATES needs a django.template.backends.django.DjangoTemplates entry. "
                "No context processors are required -- there is no request at send time."
            ),
            id="notifier.E003",
        )
    ]


@register(Tags.database)
def check_database_backend(app_configs, databases=None, **kwargs):
    """Only the databases that will actually hold notifier's tables must be PostgreSQL.

    Deliberately scoped through the router rather than looping over every alias
    in ``DATABASES``. A project is free to have a legacy MySQL replica or an
    analytics SQLite alias that notifier never touches; flagging those would
    make an unrelated connection block every management command.
    """
    from django.db import DEFAULT_DB_ALIAS, connections, router

    from notifier.models import Notification

    if databases is None:
        # Not running under `check --database`, so consult the router for the
        # one alias writes would go to.
        candidates = {router.db_for_write(Notification) or DEFAULT_DB_ALIAS}
    else:
        candidates = {
            alias
            for alias in databases
            if router.allow_migrate(alias, Notification._meta.app_label, model_name="notification")
        }

    errors = []
    for alias in sorted(candidates):
        if alias not in connections:
            continue
        vendor = connections[alias].vendor
        if vendor != "postgresql":
            errors.append(
                Error(
                    f"Database {alias!r} holds notifier's tables but uses the {vendor!r} "
                    f"backend; notifier requires PostgreSQL.",
                    hint=(
                        "ArrayField, the GIN index and the array-equality check constraints are "
                        "load-bearing. There is no portable fallback. If this alias is not meant "
                        "to hold notifier's tables, route them elsewhere with DATABASE_ROUTERS."
                    ),
                    id="notifier.E002",
                )
            )
    return errors

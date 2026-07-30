"""The system checks.

These exist because Django's own diagnostics for the two easy-to-miss
requirements are poor: a missing ``django.contrib.postgres`` produces
``postgres.E005`` once per ``ArrayField`` and never says what to do, and a
missing template engine only surfaces at render time, inside a worker.
"""

import pytest

from notifier import checks


def test_installed_apps_check_passes_when_postgres_is_installed():
    assert checks.check_installed_apps(None) == []


def test_installed_apps_check_names_the_app_and_what_to_do(monkeypatch):
    from django.apps import apps

    monkeypatch.setattr(apps, "is_installed", lambda label: False)

    errors = checks.check_installed_apps(None)

    assert len(errors) == 1
    assert errors[0].id == "notifier.E001"
    assert "django.contrib.postgres" in errors[0].msg
    assert "ArrayField" in errors[0].hint
    # It must say the headless install is still possible, since that is the
    # thing a reader will otherwise assume this breaks.
    assert "headless" in errors[0].hint


def test_template_engine_check_passes_with_a_django_backend():
    assert checks.check_template_engine(None) == []


def test_template_engine_check_explains_what_is_missing(monkeypatch):
    from django.template import engines

    monkeypatch.setattr(engines, "all", lambda: [])

    errors = checks.check_template_engine(None)

    assert len(errors) == 1
    assert errors[0].id == "notifier.E003"
    assert "TEMPLATES" in errors[0].msg
    assert "DjangoTemplates" in errors[0].hint
    # No context processors are needed, and saying so avoids a pointless hunt.
    assert "no request at send time" in errors[0].hint


def test_template_engine_check_ignores_non_django_backends(monkeypatch):
    from django.template import engines

    class Jinja:
        pass

    monkeypatch.setattr(engines, "all", lambda: [Jinja()])

    errors = checks.check_template_engine(None)

    assert len(errors) == 1
    assert errors[0].id == "notifier.E003"


def test_database_check_passes_on_postgresql():
    assert checks.check_database_backend(None) == []


def test_database_check_rejects_another_backend(monkeypatch):
    from django.db import connections

    monkeypatch.setattr(type(connections["default"]), "vendor", "sqlite", raising=False)

    errors = checks.check_database_backend(None)

    assert len(errors) == 1
    assert errors[0].id == "notifier.E002"
    assert "sqlite" in errors[0].msg
    assert "no portable fallback" in errors[0].hint


def test_the_checks_are_registered_with_django():
    from django.core.checks import registry

    registered = {check.__name__ for check in registry.registry.get_checks()}

    assert {
        "check_installed_apps",
        "check_template_engine",
        "check_database_backend",
    } <= registered


@pytest.mark.django_db
def test_manage_py_check_is_clean_for_the_test_project():
    from io import StringIO

    from django.core.management import call_command

    out = StringIO()
    call_command("check", stdout=out)

    assert "no issues" in out.getvalue()

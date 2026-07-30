"""AC-35: nothing under ``notifier`` may need auth, admin or contenttypes.

The real proof is running the suite under ``tests.settings_minimal``, which the
CI job and ``nox -s headless`` do. These tests additionally assert the property
statically, so a bad import is caught even in a full-settings run.
"""

import ast
import pathlib

import pytest

SRC = pathlib.Path(__file__).resolve().parent.parent / "src" / "notifier"

#: Apps a headless install does not have. admin.py is exempt: Django's admin
#: autodiscovery is the only thing that imports it.
FORBIDDEN = ("django.contrib.auth", "django.contrib.admin", "django.contrib.contenttypes")
EXEMPT = {"admin.py"}


def _modules():
    for path in sorted(SRC.rglob("*.py")):
        if path.name not in EXEMPT:
            yield path


@pytest.mark.parametrize("path", list(_modules()), ids=lambda p: str(p.name))
def test_ac_35_no_module_scope_import_of_absent_apps(path):
    tree = ast.parse(path.read_text())

    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            module = node.module
        elif isinstance(node, ast.Import):
            module = node.names[0].name
        else:
            continue
        if any(module.startswith(bad) for bad in FORBIDDEN):
            offenders.append(f"line {node.lineno}: {module}")

    assert not offenders, (
        f"{path.name} imports an app a headless install does not have: "
        f"{'; '.join(offenders)}. See PRD 2.10."
    )


def test_ac_35_apps_ready_does_not_import_admin():
    """``ready()`` must not pull in admin.py, only signals and checks."""
    tree = ast.parse((SRC / "apps.py").read_text())

    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.update(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)

    assert not [name for name in imported if "admin" in name], imported


def test_no_fk_to_auth_user_model():
    """The invariant that makes a userless install possible at all."""
    from notifier.models import (
        DefaultRecipient,
        Notification,
        NotificationLog,
        NotificationTemplate,
    )

    for model in (Notification, NotificationTemplate, NotificationLog, DefaultRecipient):
        for field in model._meta.get_fields():
            related = getattr(field, "related_model", None)
            if related is not None:
                assert related._meta.app_label == "notifier", (
                    f"{model.__name__}.{field.name} points outside notifier at "
                    f"{related._meta.label}."
                )
